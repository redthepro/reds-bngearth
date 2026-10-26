"""Sharing authorization, framing limits, persistent cache and cross-platform identity."""
import io
import json
from pathlib import Path
import socket
import struct
import sys
import tempfile
import threading
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import peerauth
import peerwork
import landmarks
from test_peerwork import CountingLocal, header


class FingerprintTest(unittest.TestCase):
    def test_checkout_line_endings_do_not_change_identity(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            Path(a, 'one.py').write_bytes(b'x = 1\ny = 2\n')
            Path(b, 'one.py').write_bytes(b'x = 1\r\ny = 2\r\n')
            self.assertEqual(peerwork.source_id(a), peerwork.source_id(b))
            Path(b, 'one.py').write_bytes(b'x = 2\ny = 2\n')
            self.assertNotEqual(peerwork.source_id(a), peerwork.source_id(b))

    def installed_id(self, root):
        rows = landmarks.load_catalog(root / 'mod' / 'beamng_earth')
        landmarks._excluded.cache_clear()
        try:
            with mock.patch.object(landmarks, 'catalog', return_value=rows), \
                    mock.patch.object(peerwork, '_ROOT', str(root)), \
                    mock.patch.object(peerwork, '_CODE_ID', None), \
                    mock.patch.object(peerwork, 'source_id', side_effect=AssertionError('frozen CODE_ID must be preserved')):
                result = peerwork.code_id()
                self.assertEqual(result, peerwork.code_id(), 'startup identity is cached like the installed catalog')
                self.assertTrue(result.startswith('same-frozen-source-'))
                return result
        finally:
            landmarks._excluded.cache_clear()

    def install(self, root, exclude_ids):
        Path(root, 'CODE_ID').write_text('same-frozen-source\n', encoding='ascii')
        folder = root / 'mod' / 'beamng_earth' / landmarks.VFS.lstrip('/')
        asset = folder / 'sample'
        asset.mkdir(parents=True)
        (asset / 'compiled.cdae').write_bytes(b'fixture-shape')
        (asset / 'main.materials.json').write_text('{}')
        row = {'id': 'sample', 'lat': 38.889, 'lon': -77.035, 'height_m': 169.294,
               'shape': landmarks.VFS + 'sample/compiled.cdae',
               'materials': landmarks.VFS + 'sample/main.materials.json', 'exclude_ids': exclude_ids}
        manifest = folder / 'manifest.json'
        manifest.write_text(json.dumps({'schema': 1, 'landmarks': [row]}))
        return manifest, asset, row

    def test_runtime_exclusions_extend_frozen_identity_and_missing_assets_do_not_exclude(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, asset, row = self.install(root, ['building-a'])
            installed = self.installed_id(root)
            row['exclude_ids'] = ['building-b']
            manifest.write_text(json.dumps({'landmarks': [row]}))
            self.assertNotEqual(installed, self.installed_id(root))
            (asset / 'compiled.cdae').unlink()
            missing = self.installed_id(root)
            self.assertNotEqual(installed, missing)
            manifest.write_text(json.dumps({'landmarks': []}))
            self.assertEqual(missing, self.installed_id(root), 'missing custom asset uses the same generic visuals as no exclusions')

    def test_effective_exclusions_are_order_independent_and_ignore_model_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest, asset, row = self.install(root, ['building-b', 'building-a', 'building-a'])
            first = self.installed_id(root)
            row.update(exclude_ids=['building-a', 'building-b'], title='new title', lat=39, height_m=170)
            manifest.write_text(json.dumps({'landmarks': [row]}))
            (asset / 'compiled.cdae').write_bytes(b'different-custom-geometry')
            self.assertEqual(first, self.installed_id(root), 'same procedural suppression despite different custom model metadata/bytes')

    def test_different_installed_exclusions_reject_peer_handshake(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.install(Path(a), ['building-a'])
            self.install(Path(b), ['building-b'])
            left_id, right_id = self.installed_id(Path(a)), self.installed_id(Path(b))
        left, right = socket.socketpair()
        errors = []
        def serve():
            with right:
                try:
                    peerauth.server(right, 'a' * 64, {'protocol': peerwork.PROTOCOL, 'code': right_id, 'workers': 1})
                except (ValueError, OSError) as exc:
                    errors.append(exc)
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        with left, self.assertRaises((ValueError, OSError)):
            peerauth.client(left, 'a' * 64, {'protocol': peerwork.PROTOCOL, 'code': left_id}, 1)
        thread.join(2)
        self.assertFalse(thread.is_alive())
        self.assertTrue(errors, 'mismatched installed visuals are rejected before any job')


class CacheTest(unittest.TestCase):
    def test_restart_reuses_reply_and_counts_old_files(self):
        with tempfile.TemporaryDirectory() as root:
            first = peerwork.ReplyCache(root, 256, namespace='v1')
            first.put('a' * 32, {'ok': True}, [b'x' * 150])
            second = peerwork.ReplyCache(root, 256, namespace='v1')
            self.assertEqual(second.get('a' * 32), ({'ok': True}, [b'x' * 150]))
            second.put('b' * 32, {'ok': True}, [b'y' * 150])
            self.assertIsNone(second.get('a' * 32))
            actual = sum(p.stat().st_size for p in Path(root).rglob('*.bin'))
            self.assertEqual(second.total, actual)
            self.assertLessEqual(actual, 256)

    def test_versions_and_oversized_entries(self):
        with tempfile.TemporaryDirectory() as root:
            old = peerwork.ReplyCache(root, 256, namespace='v1')
            old.put('a' * 32, {'ok': True}, [b'x' * 150])
            new = peerwork.ReplyCache(root, 256, namespace='v2')
            self.assertIsNone(new.get('a' * 32))
            new.put('b' * 32, {'ok': True}, [b'y' * 150])
            self.assertFalse(Path(old._path('a' * 32)).exists())
            new.put('c' * 32, {'ok': True}, [b'z' * 300])
            self.assertIsNone(new.get('c' * 32))
            self.assertLessEqual(new.total, 256)

    def test_corruption_is_a_miss(self):
        with tempfile.TemporaryDirectory() as root:
            cache = peerwork.ReplyCache(root, namespace='test')
            cache.put('a' * 32, {'ok': True}, [])
            Path(cache._path('a' * 32)).write_bytes(b'BNGE')
            self.assertIsNone(cache.get('a' * 32))
            self.assertEqual(cache.total, 0)


class AuthTest(unittest.TestCase):
    def connection(self, server_key='a' * 64, client_key='a' * 64):
        left, right = socket.socketpair()
        self.addCleanup(left.close)
        self.addCleanup(right.close)
        errors, channels = [], []
        identity = {'protocol': peerwork.PROTOCOL, 'code': peerwork.code_id(), 'workers': 1}

        def serve():
            try:
                channels.append(peerauth.server(right, server_key, identity))
            except (OSError, ValueError) as e:
                errors.append(e)
                right.close()
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        try:
            client, _ = peerauth.client(left, client_key, identity, 1)
        except BaseException:
            left.close()
            thread.join(2)
            raise
        thread.join(2)
        self.assertFalse(errors)
        return client, channels[0]

    def test_mutual_authentication_and_round_trip(self):
        client, server = self.connection()
        client.send({'op': 'job'})
        self.assertEqual(server.receive(), ({'op': 'job'}, []))
        server.send({'ok': True}, [b'payload'])
        self.assertEqual(client.receive(), ({'ok': True}, [b'payload']))

    def test_wrong_or_missing_secret_rejected(self):
        for key in ('', 'b' * 64):
            with self.subTest(key=bool(key)), self.assertRaises((ValueError, OSError)):
                self.connection(client_key=key)

    def test_payload_tampering_and_replay_rejected(self):
        for tamper in (False, True):
            client, server = self.connection()
            wire = {'op': 'job', '_seq': client.sent}
            wire['_mac'] = peerauth.mac(client.key, b'client' + peerwork.encode_msg(wire))
            if tamper:
                wire['op'] = 'changed'
            else:
                peerwork.send_msg(client.sock, wire)
                server.receive()
            peerwork.send_msg(client.sock, wire)
            with self.assertRaisesRegex(ValueError, 'authentication'):
                server.receive()

    def test_no_group_cannot_submit_work(self):
        with tempfile.TemporaryDirectory() as root:
            local = CountingLocal()
            pool = peerwork.SharedFarPool(local, [], {}, root, peerwork.ReplyCache(root))
            self.addCleanup(pool.io.shutdown, wait=True)
            helper = peerwork.GroupHelper(pool, lambda *_: 1, root)
            a, b = socket.socketpair()
            self.addCleanup(a.close)
            self.addCleanup(b.close)
            thread = threading.Thread(target=helper.handle, args=(b, ('127.0.0.1', 0)))
            thread.start()
            thread.join(2)
            self.assertFalse(thread.is_alive())
            self.assertEqual(a.recv(1), b'')
            self.assertEqual(local.built, [])

    def test_membership_removal_rotates_credentials(self):
        pool = peerwork.SharedFarPool(None, [], {}, None)
        self.addCleanup(pool.io.shutdown, wait=True)
        members = [('a', 'localhost', 1), ('b', 'localhost', 2), ('c', 'localhost', 3)]
        tokens = {'a': pool.session_token, 'b': 'b' * 64, 'c': 'c' * 64}
        pool.set_group('a', members, tokens=tokens)
        old_key, old_token = pool.group_key(), pool.session_token
        self.assertTrue(pool.set_group('a', members[:2], tokens=tokens))
        self.assertIsNone(pool.group_key())
        self.assertNotEqual(pool.session_token, old_token)
        tokens['a'] = pool.session_token
        pool.set_group('a', members[:2], tokens=tokens)
        self.assertNotEqual(pool.group_key(), old_key)

    def test_changed_group_revokes_existing_channel(self):
        with tempfile.TemporaryDirectory() as root:
            local = CountingLocal()
            pool = peerwork.SharedFarPool(local, [], {'roads': True, 'grading': True}, root,
                                          peerwork.ReplyCache(root))
            self.addCleanup(pool.io.shutdown, wait=True)
            tokens = {'a': pool.session_token, 'b': 'b' * 64}
            pool.set_group('a', [('a', 'localhost', 1), ('b', 'localhost', 2)], tokens=tokens)
            helper = peerwork.GroupHelper(pool, lambda *_: 1, root)
            a, b = socket.socketpair()
            self.addCleanup(a.close)
            thread = threading.Thread(target=helper.handle, args=(b, ('127.0.0.1', 0)), daemon=True)
            thread.start()
            client, _ = peerauth.client(a, pool.group_key(),
                {'protocol': peerwork.PROTOCOL, 'code': peerwork.code_id()}, 1)
            client.send(header())
            self.assertTrue(client.receive()[0]['ok'])
            pool.set_group('a', [])
            client.send(header(tile=[1, 3, 4]))
            with self.assertRaises((ConnectionError, OSError)):
                client.receive()
            thread.join(2)
            self.assertEqual(local.built, ['1_-2_3'])


class LimitsTest(unittest.TestCase):
    def test_handshake_refuses_blob_before_reading_it(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        a.sendall(peerwork.MAGIC + struct.pack('<I', 2) + b'{}' + struct.pack('<H', 1))
        with self.assertRaises(ValueError):
            peerauth.read(b, 0.2, handshake=True)

    def test_silent_peer_has_deadline(self):
        a, b = socket.socketpair()
        self.addCleanup(a.close)
        self.addCleanup(b.close)
        with self.assertRaises(TimeoutError):
            peerauth.read(b, 0.05)

    def test_total_blob_limit(self):
        packet = peerwork.encode_msg({}, [b'12345678', b'abcdefgh'])
        with self.assertRaises(ValueError):
            peerwork.recv_msg(peerwork._Bytes(packet), max_bytes=12)

    def test_forged_array_shape_rejected_before_allocation(self):
        import numpy as np
        data = io.BytesIO()
        np.lib.format.write_array_header_1_0(data, {'descr': '<f8', 'fortran_order': False, 'shape': (10**12,)})
        with self.assertRaisesRegex(ValueError, 'size'):
            peerwork._from_npy(data.getvalue())

    def test_connection_limit_rejects_excess(self):
        started, release = threading.Event(), threading.Event()

        class Handler(peerwork.socketserver.BaseRequestHandler):
            def handle(self):
                started.set()
                release.wait(2)
        with mock.patch.object(peerauth, 'MAX_CONNECTIONS', 1):
            server = peerauth.BoundedServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with socket.create_connection(server.server_address, timeout=1):
                self.assertTrue(started.wait(1))
                with socket.create_connection(server.server_address, timeout=1) as excess:
                    self.assertEqual(excess.recv(1), b'')
        finally:
            release.set()
            server.shutdown()
            server.server_close()
            thread.join(2)


class LifecycleTest(unittest.TestCase):
    def test_leaving_closes_listener_and_revokes_session(self):
        from sharing import SharingServerMixin

        class Server(SharingServerMixin):
            pass
        with tempfile.TemporaryDirectory() as root:
            server = Server()
            server.cache_dir, server.anchors = root, {}
            server.share_lock, server.group_helper = threading.Lock(), None
            server.far_pool = peerwork.SharedFarPool(None, [], {}, root, peerwork.ReplyCache(root))
            self.addCleanup(server.far_pool.io.shutdown, wait=True)
            self.addCleanup(server.share_stop)
            with mock.patch('sharing.share_port', return_value=0):
                info = server.share_start()
            self.assertTrue(info['port'])
            self.assertTrue(peerauth.token(info['token']))
            server.far_pool.set_group('a', [('a', 'localhost', info['port']), ('b', 'localhost', 1)],
                                     tokens={'a': info['token'], 'b': 'b' * 64})
            server.share_stop()
            self.assertIsNone(server.far_pool.group_key())
            self.assertFalse(server.far_pool.sharing)
            self.assertNotEqual(server.far_pool.session_token, info['token'])
            with self.assertRaises(OSError):
                socket.create_connection(('127.0.0.1', info['port']), timeout=0.2)


if __name__ == '__main__':
    unittest.main()
