"""Headless voice framing and certificate tests; never opens audio devices."""
import struct
import unittest
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voice_wire as w
from voice_client import MumbleClient


class WireTest(unittest.TestCase):
    def test_integer_boundaries(self):
        for n in (0, 127, 128, 16383, 16384, 0x1fffff, 0x200000, 0xfffffff, 0x10000000, 2**32, 2**64 - 1):
            self.assertEqual(w.read_mvar(w.mvar(n)), (n, len(w.mvar(n))))
            self.assertEqual(w.read_varint(w.varint(n)), (n, len(w.varint(n))))
        for bad in (-1, 2**64):
            with self.assertRaises(ValueError):
                w.mvar(bad)
        for data in (b'', b'\x80', b'\xf4\x00', b'\xff'):
            with self.assertRaises(ValueError):
                w.read_mvar(data)

    def test_fragmented_control_and_unknown_fields(self):
        payload = w.pb((1, 42), (2, 'hello'), (2, b'world'))
        data = w.control(w.USER, payload)
        decoder = w.ControlDecoder()
        got = []
        for byte in data:
            got.extend(decoder.feed(bytes([byte])))
        self.assertEqual(got, [(w.USER, payload)])
        self.assertEqual(w.unpb(payload), {1: [42], 2: [b'hello', b'world']})
        with self.assertRaises(ValueError):
            w.ControlDecoder().feed(struct.pack('!HI', 1, w.MAX_CONTROL + 1))
        for bad in (b'\x00', b'\x0a\xff', b'\x09\x00', b'\x08' + b'\xff' * 10):
            with self.assertRaises(ValueError):
                w.unpb(bad)

    def test_opus_end_and_position(self):
        client = w.audio(20, b'abc', 1, (110., 220., 330.), True)
        server = client[:1] + w.mvar(501) + client[1:]
        self.assertEqual(w.read_audio(server), (501, 20, b'abc', (110., 220., 330.), True))
        for data in (server[:-1], server + b'x', b'\x00', b'\x80\x01\x00\x84\x01'):
            with self.assertRaises(ValueError):
                w.read_audio(data)
        with self.assertRaises(ValueError):
            w.audio(0, b'x', position=(float('nan'), 0, 0))

    def test_bad_pin_sends_no_credentials(self):
        sock = MagicMock()
        sock.getpeercert.return_value = b'untrusted certificate'
        context = MagicMock()
        context.wrap_socket.return_value = sock
        client = MumbleClient('host', 64738, '0' * 64, 'user', 'secret', 'p', lambda *a: None, lambda *a: None)
        with patch('socket.create_connection'), patch('ssl.SSLContext', return_value=context):
            with self.assertRaises(Exception):
                client.connect()
        sock.send.assert_not_called()
        self.assertEqual(client.pending, b'')
        sock.close.assert_called_once()

    def test_only_nearby_targets_and_bounded_backlog(self):
        client = MumbleClient('host', 64738, '0' * 64, 'user', 'secret', 'p', lambda *a: None, lambda *a: None)
        client.ready, client.session = True, 1
        client.users = {1: 100, 2: 103, 3: 106}
        client.set_targets({100, 103})
        self.assertEqual(client.targets, (2,))
        client.pending = bytearray(65536)
        with self.assertRaises(ConnectionError):
            client.send_audio(b'abc')


if __name__ == '__main__':
    unittest.main()
