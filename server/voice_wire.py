"""Bounded Mumble 1.3-compatible TLS control and Opus framing.

Only the protobuf wire primitives needed by our client are implemented here. Field
numbers follow mumble-voip/mumble/src/Mumble.proto (BSD-3-Clause). Unknown fields
are skipped, malformed/oversized frames fail closed. Audio uses Mumble's standard
TCP tunnel, keeping voice independent from the game's socket and Lua event queue.
"""
import math
import struct

MAX_CONTROL = 1024 * 1024
MAX_AUDIO = 4096
VERSION, AUDIO, AUTH, PING, REJECT, SYNC = 0, 1, 2, 3, 4, 5
CHANNEL_REMOVE, CHANNEL, USER_REMOVE, USER = 6, 7, 8, 9
DENIED, CRYPT, TARGET, CODEC = 12, 15, 19, 21


def varint(value):
    if not isinstance(value, int) or not 0 <= value < 1 << 64:
        raise ValueError('invalid protobuf integer')
    out = bytearray()
    while value > 127:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def read_varint(data, at=0):
    value = 0
    for shift in range(0, 70, 7):
        if at >= len(data):
            raise ValueError('truncated protobuf integer')
        byte = data[at]
        at += 1
        value |= (byte & 127) << shift
        if not byte & 128:
            if value >= 1 << 64:
                raise ValueError('protobuf integer overflow')
            return value, at
    raise ValueError('protobuf integer overflow')


def pb(*fields):
    """Encode (field_number, int|str|bytes) pairs, including repeated fields."""
    out = bytearray()
    for number, value in fields:
        if not 1 <= number < 1 << 29:
            raise ValueError('invalid protobuf field')
        if isinstance(value, int):
            out += varint(number << 3) + varint(value)
        else:
            value = value.encode('utf-8') if isinstance(value, str) else bytes(value)
            out += varint((number << 3) | 2) + varint(len(value)) + value
    if len(out) > MAX_CONTROL:
        raise ValueError('oversized protobuf message')
    return bytes(out)


def unpb(data):
    if len(data) > MAX_CONTROL:
        raise ValueError('oversized protobuf message')
    out, at = {}, 0
    while at < len(data):
        tag, at = read_varint(data, at)
        number, kind = tag >> 3, tag & 7
        if not number:
            raise ValueError('zero protobuf field')
        if kind == 0:
            value, at = read_varint(data, at)
        elif kind in (1, 5):
            n = 8 if kind == 1 else 4
            value, at = data[at:at + n], at + n
            if len(value) != n:
                raise ValueError('truncated fixed protobuf field')
        elif kind == 2:
            n, at = read_varint(data, at)
            if n > MAX_CONTROL or at + n > len(data):
                raise ValueError('truncated protobuf field')
            value, at = data[at:at + n], at + n
        else:
            raise ValueError('unsupported protobuf wire type')
        out.setdefault(number, []).append(value)
    return out


def one(fields, number, default=None):
    return fields.get(number, [default])[-1]


def text(fields, number, default=''):
    value = one(fields, number)
    return default if value is None else value.decode('utf-8', errors='replace')[:512]


def control(kind, data):
    if not 0 <= kind <= 30 or len(data) > MAX_CONTROL:
        raise ValueError('invalid control frame')
    return struct.pack('!HI', kind, len(data)) + data


class ControlDecoder:
    def __init__(self):
        self.data = bytearray()

    def feed(self, data):
        self.data += data
        out = []
        while len(self.data) >= 6:
            kind, n = struct.unpack_from('!HI', self.data)
            if n > MAX_CONTROL or kind > 30:
                raise ValueError('invalid control frame')
            if len(self.data) < n + 6:
                break
            out.append((kind, bytes(self.data[6:n + 6])))
            del self.data[:n + 6]
        if len(self.data) > MAX_CONTROL + 6:
            raise ValueError('control buffer overflow')
        return out


def mvar(value):
    """Mumble's packet integer encoding, distinct from protobuf varints."""
    if not isinstance(value, int) or not 0 <= value <= 0xffffffffffffffff:
        raise ValueError('invalid audio integer')
    if value < 0x80:
        return bytes([value])
    if value < 0x4000:
        return bytes([(value >> 8) | 0x80, value & 255])
    if value < 0x200000:
        return bytes([(value >> 16) | 0xc0, (value >> 8) & 255, value & 255])
    if value < 0x10000000:
        return bytes([(value >> 24) | 0xe0]) + value.to_bytes(4, 'big')[1:]
    return (b'\xf0' + value.to_bytes(4, 'big')) if value < 1 << 32 else b'\xf4' + value.to_bytes(8, 'big')


def read_mvar(data, at=0):
    if at >= len(data):
        raise ValueError('truncated audio integer')
    first, at = data[at], at + 1
    if first < 0x80:
        return first, at
    if first < 0xc0:
        n, value = 1, first & 0x3f
    elif first < 0xe0:
        n, value = 2, first & 0x1f
    elif first < 0xf0:
        n, value = 3, first & 0x0f
    elif first == 0xf0:
        n, value = 4, 0
    elif first == 0xf4:
        n, value = 8, 0
    else:
        raise ValueError('negative/reserved audio integer')
    if at + n > len(data):
        raise ValueError('truncated audio integer')
    for byte in data[at:at + n]:
        value = (value << 8) | byte
    return value, at + n


def audio(sequence, packet, target=0, position=None, end=False):
    if not 0 <= target <= 30 or len(packet) > 512:
        raise ValueError('invalid audio packet')
    out = bytes([(4 << 5) | target]) + mvar(sequence) + mvar(len(packet) | (0x2000 if end else 0)) + packet
    if position is not None:
        if len(position) != 3 or not all(math.isfinite(v) and abs(v) <= 7e6 for v in position):
            raise ValueError('invalid voice position')
        out += struct.pack('<3f', *position)
    return out


def read_audio(data):
    """Decode a server-to-client legacy Opus packet; return session, sequence, PCM packet, position, end."""
    if not data or len(data) > MAX_AUDIO or data[0] >> 5 != 4:
        raise ValueError('unsupported audio codec or size')
    session, at = read_mvar(data, 1)
    sequence, at = read_mvar(data, at)
    size, at = read_mvar(data, at)
    if size & ~0x3fff:
        raise ValueError('invalid Opus length')
    end, n = bool(size & 0x2000), size & 0x1fff
    if n > 512 or at + n > len(data):
        raise ValueError('truncated/oversized Opus data')
    packet, at = bytes(data[at:at + n]), at + n
    position = None
    if len(data) - at == 12:
        position = struct.unpack_from('<3f', data, at)
        if not all(math.isfinite(v) and abs(v) <= 7e6 for v in position):
            raise ValueError('invalid remote position')
    elif at != len(data):
        raise ValueError('unexpected Opus trailing data')
    return session, sequence, packet, position, end
