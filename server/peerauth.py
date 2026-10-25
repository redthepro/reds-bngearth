"""Authenticated compute-sharing channels and bounded TCP listeners.

BeamMP distributes random session credentials only to the current anchor group.
Standalone helpers use BNGE_PEER_TOKEN. Credentials never travel on this socket:
fresh nonces authenticate both endpoints, and direction/sequence-bound MACs protect
every subsequent message. Map data is authenticated, but is not encrypted.
"""
import hashlib
import hmac
import secrets
import socketserver
import threading
import time

HANDSHAKE_S = 5.0
READ_S = 30.0
MAX_CONNECTIONS = 32


def token(value):
    return isinstance(value, str) and len(value) == 64 and all(c in '0123456789abcdef' for c in value)


class Deadline:
    def __init__(self, sock, seconds):
        self.sock, self.until = sock, time.monotonic() + seconds

    def recv(self, n):
        left = self.until - time.monotonic()
        if left <= 0:
            raise TimeoutError('peer read deadline')
        self.sock.settimeout(left)
        return self.sock.recv(n)


def read(sock, seconds=READ_S, handshake=False, allow_blobs=True):
    from peerwork import recv_msg
    return recv_msg(Deadline(sock, seconds), max_bytes=(4096 if handshake else 64 << 20),
                    allow_blobs=allow_blobs and not handshake)


def mac(key, data):
    return hmac.new(key.encode(), data, hashlib.sha256).hexdigest()


class Channel:
    def __init__(self, sock, key, role, timeout=READ_S):
        self.sock, self.key, self.role, self.timeout = sock, key, role, timeout
        self.sent = self.received = 0

    def send(self, header, blobs=()):
        from peerwork import encode_msg, send_msg
        wire = dict(header, _seq=self.sent)
        wire['_mac'] = mac(self.key, self.role.encode() + encode_msg(wire, blobs))
        self.sock.settimeout(self.timeout)
        send_msg(self.sock, wire, blobs)
        self.sent += 1

    def receive(self):
        from peerwork import encode_msg
        wire, blobs = read(self.sock, self.timeout, allow_blobs=self.role == 'client')
        signature = wire.pop('_mac', '')
        other = b'server' if self.role == 'client' else b'client'
        expected = mac(self.key, other + encode_msg(wire, blobs))
        if (wire.get('_seq') != self.received or not token(signature)
                or not hmac.compare_digest(signature, expected)):
            raise ValueError('peer authentication failed')
        self.received += 1
        wire.pop('_seq')
        return wire, blobs


def server(sock, key, identity):
    from peerwork import send_msg
    if not token(key):
        raise ValueError('no authorized sharing group')
    hello, blobs = read(sock, HANDSHAKE_S, handshake=True)
    nonce = hello.get('nonce')
    if (blobs or hello.get('op') != 'hello' or not token(nonce)
            or any(hello.get(k) != identity[k] for k in ('protocol', 'code'))):
        raise ValueError('incompatible peer')
    challenge = secrets.token_hex(32)
    transcript = (nonce + challenge + identity['code']).encode()
    send_msg(sock, dict(identity, nonce=challenge, proof=mac(key, b'server' + transcript)))
    proof, blobs = read(sock, HANDSHAKE_S, handshake=True)
    if blobs or not token(proof.get('proof')) or not hmac.compare_digest(proof['proof'], mac(key, b'client' + transcript)):
        raise ValueError('peer authentication failed')
    channel = Channel(sock, mac(key, b'channel' + transcript), 'server')
    channel.send(dict(identity, ok=True))
    return channel


def client(sock, key, identity, timeout):
    from peerwork import send_msg
    if not token(key):
        raise ValueError('compute sharing requires a 64-character hex credential')
    nonce = secrets.token_hex(32)
    sock.settimeout(HANDSHAKE_S)
    send_msg(sock, dict(identity, op='hello', nonce=nonce))
    hello, blobs = read(sock, HANDSHAKE_S, handshake=True)
    challenge = hello.get('nonce')
    if (blobs or not token(challenge)
            or any(hello.get(k) != identity[k] for k in ('protocol', 'code'))):
        raise ValueError('incompatible peer')
    transcript = (nonce + challenge + identity['code']).encode()
    if not token(hello.get('proof')) or not hmac.compare_digest(hello['proof'], mac(key, b'server' + transcript)):
        raise ValueError('peer authentication failed')
    send_msg(sock, {'proof': mac(key, b'client' + transcript)})
    channel = Channel(sock, mac(key, b'channel' + transcript), 'client', timeout)
    confirmed, blobs = channel.receive()
    if blobs or not confirmed.get('ok'):
        raise ValueError('peer authentication failed')
    return channel, confirmed


class BoundedServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = False

    def __init__(self, *args, **kwargs):
        self.slots = threading.BoundedSemaphore(MAX_CONNECTIONS)
        super().__init__(*args, **kwargs)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()
