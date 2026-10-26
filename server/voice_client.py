"""Small Mumble TLS client. No microphone access, background threads or global state.

The owner polls each connection on its audio worker. Certificate pinning happens
before credentials leave the machine. Audio and control queues have hard limits.
"""
import hashlib
import hmac
import socket
import ssl
import time

import voice_wire as w


class MumbleClient:
    def __init__(self, host, port, fingerprint, username, token, role, on_audio, on_remove):
        if not isinstance(host, str) or not 1 <= len(host) <= 253 or any(c.isspace() for c in host):
            raise ValueError('invalid voice host')
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise ValueError('invalid voice port')
        self.host, self.port = host, port
        self.fingerprint = fingerprint.lower().replace(':', '')
        if len(self.fingerprint) != 64 or any(c not in '0123456789abcdef' for c in self.fingerprint):
            raise ValueError('invalid voice certificate fingerprint')
        if role not in ('p', 's', 'g'):
            raise ValueError('invalid voice role')
        self.username, self.token, self.role = username, token, role
        self.on_audio, self.on_remove = on_audio, on_remove
        self.sock = None
        self.decoder = w.ControlDecoder()
        self.pending = bytearray()
        self.users = {}
        self.channels = {}
        self.session = None
        self.ready = False
        self.sequence = 0
        self.targets = ()
        self.ping_at = 0.
        self.audio_backlog_at = None

    def connect(self):
        self.close()
        raw = socket.create_connection((self.host, self.port), timeout=3)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE  # exact certificate pin checked before Authenticate
        try:
            sock = context.wrap_socket(raw, server_hostname=self.host)
            actual = hashlib.sha256(sock.getpeercert(binary_form=True)).hexdigest()
            if not hmac.compare_digest(actual, self.fingerprint):
                raise ssl.SSLError('voice server certificate changed')
            sock.setblocking(False)
            self.sock = sock
            self.queue(w.VERSION, w.pb((1, 0x010300), (2, 'BeamNG.Earth voice 1'), (3, 'companion')))
            self.queue(w.AUTH, w.pb((1, self.username), (2, self.token), (5, 1)))
            self.ping_at = time.monotonic()
        except BaseException:
            raw.close()
            if 'sock' in locals():
                sock.close()
            raise

    def queue(self, kind, payload):
        frame = w.control(kind, payload)
        if len(self.pending) + len(frame) > 65536:
            raise ConnectionError('voice output stalled')
        self.pending += frame

    def set_targets(self, userids):
        wanted = tuple(sorted(s for s, u in self.users.items() if u in userids and s != self.session))[:100]
        if wanted != self.targets:
            target = w.pb(*[(1, s) for s in wanted])
            self.queue(w.TARGET, w.pb((1, 1), (2, target)))
            self.targets = wanted

    def send_audio(self, packet, position=None, end=False):
        if not self.ready or (self.role == 'p' and not self.targets):
            return
        now = time.monotonic()
        if self.audio_backlog_at is not None and now - self.audio_backlog_at > .2:
            raise ConnectionError('voice audio output stalled')
        if self.audio_backlog_at is None:
            self.audio_backlog_at = now
        self.queue(w.AUDIO, w.audio(self.sequence, packet, 1 if self.role == 'p' else 0, position, end))
        self.sequence += 2

    def _message(self, kind, payload):
        if kind == w.AUDIO:
            session, seq, packet, pos, end = w.read_audio(payload)
            uid = self.users.get(session)
            if uid is not None and session != self.session:
                self.on_audio(uid, seq, packet, pos, end)
            return
        fields = w.unpb(payload)
        if kind == w.REJECT:
            raise ConnectionError('voice authentication rejected')
        if kind == w.SYNC:
            self.session = w.one(fields, 1)
            self.ready = True
            # Positional context matches across all nearby clients; never game-local coordinates.
            self.queue(w.USER, w.pb((1, self.session), (12, b'BeamNG.Earth/ECEF/v1')))
        elif kind == w.USER:
            session, uid = w.one(fields, 1), w.one(fields, 4)
            if isinstance(session, int) and isinstance(uid, int):
                if len(self.users) >= 320 and session not in self.users:
                    raise ValueError('oversized voice roster')
                self.users[session] = uid
        elif kind == w.USER_REMOVE:
            uid = self.users.pop(w.one(fields, 1), None)
            if uid is not None:
                self.on_remove(uid)
        elif kind == w.CHANNEL:
            id = w.one(fields, 1)
            if isinstance(id, int) and 3 in fields:
                if len(self.channels) >= 512 and id not in self.channels:
                    raise ValueError('oversized voice channel list')
                self.channels[id] = w.text(fields, 3)
        elif kind == w.CHANNEL_REMOVE:
            self.channels.pop(w.one(fields, 1), None)

    def poll(self):
        if self.sock is None:
            return
        if time.monotonic() - self.ping_at >= 5:
            self.queue(w.PING, w.pb((1, time.monotonic_ns() // 1000)))
            self.ping_at = time.monotonic()
        try:
            if self.pending:
                n = self.sock.send(self.pending)
                del self.pending[:n]
                if not self.pending:
                    self.audio_backlog_at = None
        except (ssl.SSLWantWriteError, ssl.SSLWantReadError, BlockingIOError):
            pass
        # Bound work per poll so a busy server cannot starve the microphone lease.
        for _ in range(4):
            try:
                data = self.sock.recv(16384)
            except (ssl.SSLWantReadError, ssl.SSLWantWriteError, BlockingIOError):
                break
            if not data:
                raise ConnectionError('voice connection closed')
            for kind, payload in self.decoder.feed(data):
                self._message(kind, payload)

    def close(self):
        if self.sock:
            self.sock.close()
        self.sock = None
        self.ready = False
        self.users.clear()
        self.channels.clear()
        self.pending.clear()
        self.decoder = w.ControlDecoder()
        self.targets = ()
        self.audio_backlog_at = None
