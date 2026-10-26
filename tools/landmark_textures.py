"""Offline GPU-ready landmark textures: BC7 sRGB, BC5 normals, BC4 linear data.

BeamNG materials keep their .color/.normal/.data.png references. Only matching DDS
files are shipped; the engine resolves those without running its texture cooker.
Color blocks reuse earthfast's BC7 encoder. Pillow's BC5 encoder supplies two BC4
blocks per tile; single-channel data keeps its red block. All formats are UNORM.
Offline DDS conversion requires Pillow>=11.2.1 (the BC5 writer); runtime does not.
"""
import io
from pathlib import Path
import struct
import sys
import re

import numpy as np
from PIL import Image
from PIL import __version__ as PILLOW_VERSION

HAS_BC5_WRITER = tuple(map(int, re.findall(r'\d+', PILLOW_VERSION)[:3])) >= (11, 2, 1)


def _linear(values):
    return np.where(values <= .04045, values / 12.92, ((values + .055) / 1.055) ** 2.4)


def _srgb(values):
    return np.where(values <= .0031308, values * 12.92, 1.055 * np.maximum(values, 0) ** (1 / 2.4) - .055)


def texture_mips(image, semantic):
    """Full rectangular POT chain; color uses linear light and normals renormalize."""
    mode = {'color': 'RGBA', 'normal': 'RGB', 'data': 'L'}[semantic]
    source = np.asarray(image.convert(mode), np.uint8)
    if source.ndim == 2:
        source = source[..., None]
    current = source.astype(np.float32) / 255
    if semantic == 'color':
        current[..., :3] = _linear(current[..., :3]) * current[..., 3:4]
    elif semantic == 'normal':
        current = current * 2 - 1
    first = True
    while True:
        if first:
            yield source
            first = False
        else:
            if semantic == 'color':
                rgba = current.copy()
                rgba[..., :3] = _srgb(np.divide(current[..., :3], current[..., 3:4],
                                                out=np.zeros_like(current[..., :3]), where=current[..., 3:4] > 1e-8))
            elif semantic == 'normal':
                normal = current / np.maximum(np.linalg.norm(current, axis=2, keepdims=True), 1e-8)
                rgba = (normal + 1) / 2
            else:
                rgba = current
            yield np.rint(np.clip(rgba, 0, 1) * 255).astype(np.uint8)
        height, width, channels = current.shape
        if height == width == 1:
            break
        sy, sx = min(2, height), min(2, width)
        current = current.reshape(height // sy, sy, width // sx, sx, channels).mean((1, 3))


def _bc5_blocks(array):
    # Pillow writes a DX10 header plus native BC5 blocks, without mipmaps.
    if not HAS_BC5_WRITER:
        raise RuntimeError('Offline landmark DDS conversion requires Pillow>=11.2.1 for BC4/BC5; update the conversion environment')
    if array.shape[2] == 1:
        array = np.repeat(array, 3, axis=2)
    height, width = array.shape[:2]
    array = np.pad(array, ((0, max(4, height) - height), (0, max(4, width) - width), (0, 0)), mode='edge')
    output = io.BytesIO()
    Image.fromarray(array, 'RGB').save(output, format='DDS', pixel_format='BC5')
    data = output.getvalue()
    if data[:4] != b'DDS ' or data[84:88] != b'DX10':
        raise ValueError('Pillow did not produce a BC5 DDS')
    return data[148:]


def encode_dds(image, semantic):
    """Encode a full mip chain with explicit DXGI color/data semantics."""
    width, height = image.size
    if min(width, height) < 16 or any(size & (size - 1) for size in image.size):
        raise ValueError('DDS source dimensions must be powers of two, at least16')
    dxgi, block_size = {'color': (99, 16), 'normal': (83, 16), 'data': (80, 8)}[semantic]
    if semantic == 'color':
        server_path = str(Path(__file__).resolve().parents[1] / 'server')
        if server_path not in sys.path:
            sys.path.insert(0, server_path)
        import bc7
    body, count = [], 0
    for level in texture_mips(image, semantic):
        if semantic == 'color':
            encoded = bc7.encode(level)
        else:
            encoded = _bc5_blocks(level)
            if semantic == 'data':
                encoded = np.frombuffer(encoded, np.uint8).reshape(-1, 16)[:, :8].copy().tobytes()
        expected = max(1, (level.shape[1] + 3) // 4) * max(1, (level.shape[0] + 3) // 4) * block_size
        if len(encoded) != expected:
            raise ValueError('Texture encoder returned an invalid compressed block count')
        body.append(encoded)
        count += 1
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000
    header = struct.pack('<4s7I', b'DDS ', 124, flags, height, width,
                         max(1, width // 4) * max(1, height // 4) * block_size, 0, count)
    header += b'\0' * 44
    header += struct.pack('<2I4s5I', 32, 0x4, b'DX10', 0, 0, 0, 0, 0)
    header += struct.pack('<5I', 0x401008, 0, 0, 0, 0)
    header += struct.pack('<5I', dxgi, 3, 0, 1, 0)
    return header + b''.join(body)


def compile_textures(directory, source_out=None):
    """Replace exported PNG sources with DDS; optional source_out is a separate QA folder."""
    directory = Path(directory)
    if source_out:
        source_out = Path(source_out).resolve()
        if source_out == directory.resolve() or directory.resolve() in source_out.parents:
            raise ValueError('Texture QA sources must be outside the shipped texture directory')
        source_out.mkdir(parents=True, exist_ok=True)
    records = []
    for path in sorted(directory.glob('*.png')):
        semantic = path.name.rsplit('.', 2)[-2]
        if semantic not in ('color', 'normal', 'data'):
            raise ValueError('Unknown texture semantic: ' + path.name)
        with Image.open(path) as image:
            # A solid map needs only the minimum16px texture, regardless of source resolution.
            array = np.asarray(image)
            if np.all(array == array[0, 0]):
                image = image.resize((16, 16), Image.Resampling.NEAREST)
            data = encode_dds(image, semantic)
            dimensions = list(image.size)
        if source_out:
            (source_out / path.name).write_bytes(path.read_bytes())
        path.with_suffix('.dds').write_bytes(data)
        path.unlink()
        records.append(dict(source=path.name, file=path.with_suffix('.dds').name, size=dimensions,
                            dxgi_format={'color': 99, 'normal': 83, 'data': 80}[semantic], bytes=len(data)))
    return records
