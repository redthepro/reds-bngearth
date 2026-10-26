#!/usr/bin/env python3
"""Convert an attributed static GLB to a BeamNG compiled shape with a real camera LOD.

Run with the project's Python environment, Pillow>=11.2.1/numpy and Blender installed:
  python tools/convert_landmark.py source.glb --out build/monument --asset-id washington_monument \
    --metadata source-metadata.json --blender /path/to/blender

Outputs compiled.cdae, main.materials.json, cooked DDS textures/, and landmark.json. Source units
are retained unless --scale, --target-height or --target-width is explicitly supplied.
Blender imports glTF's Y-up axes into Z-up. Optional --tilt-x, --tilt-y, then --yaw
rotate around world X, Y, Z before measuring dimensions and centering. --origin-xy
sets the geographic anchor in these oriented source units, before uniform scaling.
--crop-min/--crop-max clip the oriented source geometry before making camera LODs.
Material slots are namespaced per asset; original source names remain in landmark.json.
Only static meshes and UV0 textures are supported; unsupported texture transforms fail
explicitly. A specular/glossiness source retains diffuse color and approximates roughness.
"""
import argparse
import base64
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import struct
import subprocess
import sys
import tempfile

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]


def read_glb(path):
    data = Path(path).read_bytes()
    if len(data) < 20 or struct.unpack_from('<4sII', data) != (b'glTF', 2, len(data)):
        raise ValueError('Expected a complete glTF 2.0 binary file')
    chunks, at = [], 12
    while at < len(data):
        if len(data) - at < 8:
            raise ValueError('Truncated GLB chunk header')
        size, kind = struct.unpack_from('<II', data, at)
        at += 8
        if size % 4 or at + size > len(data):
            raise ValueError('Invalid GLB chunk length')
        chunks.append((kind, data[at:at + size]))
        at += size
    if not chunks or chunks[0][0] != 0x4E4F534A:
        raise ValueError('The first GLB chunk must contain JSON')
    return json.loads(chunks[0][1]), chunks[1:], hashlib.sha256(data).hexdigest()


def write_glb(path, document, chunks):
    raw = json.dumps(document, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    raw += b' ' * (-len(raw) % 4)
    body = b''.join(struct.pack('<II', len(value), kind) + value
                    for kind, value in [(0x4E4F534A, raw)] + chunks)
    Path(path).write_bytes(struct.pack('<4sII', b'glTF', 2, 12 + len(body)) + body)


def linear_to_srgb(values):
    values = np.clip(np.asarray(values, float), 0, 1)
    return np.where(values <= .0031308, values * 12.92, 1.055 * values ** (1 / 2.4) - .055)


def srgb_to_linear(values):
    values = np.asarray(values, float)
    return np.where(values <= .04045, values / 12.92, ((values + .055) / 1.055) ** 2.4)


def dimensions(size, maximum):
    """BeamNG's texture cooker requires power-of-two dimensions, at least 16px."""
    return tuple(max(16, min(maximum, 2 ** int(math.floor(math.log2(max(v, 1)))))) for v in size)


def resize_channels(array, size):
    if (array.shape[1], array.shape[0]) == size:
        return array
    return np.stack([np.asarray(Image.fromarray(array[..., c].astype(np.float32), mode='F').resize(
        size, Image.Resampling.LANCZOS)) for c in range(array.shape[2])], axis=-1)


def texture_image(document, chunks, info):
    if info.get('texCoord', 0) != 0 or info.get('extensions'):
        raise ValueError('Only UV0 textures without texture transforms are supported')
    texture = document['textures'][info['index']]
    source = texture.get('source')
    if source is None:
        raise ValueError('Compressed/extension-only texture sources are not supported')
    image = document['images'][source]
    if 'bufferView' in image:
        view = document['bufferViews'][image['bufferView']]
        if view.get('buffer', 0) != 0:
            raise ValueError('External GLB buffers are not supported')
        binary = next((blob for kind, blob in chunks if kind == 0x004E4942), None)
        if binary is None:
            raise ValueError('Missing GLB binary buffer')
        start, length = view.get('byteOffset', 0), view['byteLength']
        raw = binary[start:start + length]
        if len(raw) != length:
            raise ValueError('Image buffer view exceeds GLB data')
    elif image.get('uri', '').startswith('data:') and ';base64,' in image['uri']:
        raw = base64.b64decode(image['uri'].split(',', 1)[1], validate=True)
    else:
        raise ValueError('GLB images must be embedded, not external paths/URLs')
    return Image.open(io.BytesIO(raw)).convert('RGBA')


def normalize_duplicate_uvs(document, chunks):
    """Allow alternate UV slots only when every affected primitive exactly matches UV0."""
    binary = next((blob for kind, blob in chunks if kind == 0x004E4942), b'')

    def uv_array(index):
        accessor = document['accessors'][index]
        if accessor.get('type') != 'VEC2' or accessor.get('componentType') != 5126 or accessor.get('sparse'):
            raise ValueError('Alternate UV slots require ordinary float VEC2 accessors')
        view = document['bufferViews'][accessor['bufferView']]
        if view.get('buffer', 0) != 0:
            raise ValueError('External UV buffers are not supported')
        return np.ndarray((accessor['count'], 2), dtype='<f4', buffer=binary,
                          offset=view.get('byteOffset', 0) + accessor.get('byteOffset', 0),
                          strides=(view.get('byteStride', 8), 4))

    warnings = []
    for index, material in enumerate(document.get('materials', [])):
        pbr = material.get('pbrMetallicRoughness', {})
        spec = material.get('extensions', {}).get('KHR_materials_pbrSpecularGlossiness', {})
        infos = [material.get('normalTexture'), material.get('occlusionTexture'), material.get('emissiveTexture'),
                 pbr.get('baseColorTexture'), pbr.get('metallicRoughnessTexture'), spec.get('diffuseTexture')]
        for info in filter(None, infos):
            slot = info.get('texCoord', 0)
            if slot == 0:
                continue
            primitives = [p for mesh in document.get('meshes', []) for p in mesh['primitives'] if p.get('material') == index]
            for primitive in primitives:
                attributes = primitive['attributes']
                if 'TEXCOORD_0' not in attributes or 'TEXCOORD_' + str(slot) not in attributes or not np.array_equal(
                        uv_array(attributes['TEXCOORD_0']), uv_array(attributes['TEXCOORD_' + str(slot)])):
                    raise ValueError('Only UV0 or exactly duplicate UV texture coordinates are supported')
            info['texCoord'] = 0
            warnings.append(material.get('name', str(index)) + ': duplicate UV' + str(slot) + ' verified and mapped to UV0')
    return warnings


def material_assets(document, chunks, out, virtual_path, asset_id, maximum):
    materials, records, warnings = {}, [], []
    original = document.get('materials', [])
    # A named default material also covers primitives without a material slot.
    original = original + [{'name': '__default__', 'pbrMetallicRoughness': {'metallicFactor': 0}}]
    material_ids = {'__default__': len(original) - 1}
    for index, source in enumerate(original):
        original_name = source.get('name', 'Material_' + str(index))
        name = 'bne_lm_' + asset_id + '_' + str(index) + '_' + re.sub(r'[^A-Za-z0-9_]', '_', original_name)[:30]
        material_ids[name] = index
        pbr = source.get('pbrMetallicRoughness', {})
        spec = source.get('extensions', {}).get('KHR_materials_pbrSpecularGlossiness')
        if spec:
            pbr = dict(baseColorFactor=spec.get('diffuseFactor', [1, 1, 1, 1]),
                       metallicFactor=0, roughnessFactor=1 - spec.get('glossinessFactor', 1))
            if spec.get('diffuseTexture'):
                pbr['baseColorTexture'] = spec['diffuseTexture']
            warnings.append(original_name + ': specular/glossiness approximated as diffuse/nonmetal roughness')
        if 'KHR_materials_unlit' in source.get('extensions', {}):
            # Photogrammetry often uses unlit display materials. Keep its albedo but
            # make this explicit: a world landmark should participate in day/night lighting.
            pbr = dict(pbr, metallicFactor=0, roughnessFactor=1)
            warnings.append(original_name + ': unlit source converted to a lit matte world material')
        factor = np.asarray(pbr.get('baseColorFactor', [1, 1, 1, 1]), float)
        if factor.shape != (4,) or not np.isfinite(factor).all() or np.any(factor < 0) or np.any(factor > 1):
            raise ValueError('Invalid linear base color factor')
        image = texture_image(document, chunks, pbr['baseColorTexture']) if pbr.get('baseColorTexture') else Image.new('RGBA', (16, 16), 'white')
        rgba = np.asarray(image, float) / 255
        rgba[..., :3] = srgb_to_linear(rgba[..., :3]) * factor[:3]
        rgba[..., 3] *= factor[3]
        rgba = resize_channels(rgba, dimensions(image.size, maximum))
        rgba[..., :3] = linear_to_srgb(rgba[..., :3])
        base_file = name + '.color.png'
        Image.fromarray(np.rint(np.clip(rgba, 0, 1) * 255).astype(np.uint8), 'RGBA').save(out / 'textures' / base_file)
        texture_path = lambda filename: str(PurePosixPath(virtual_path) / 'textures' / filename)
        stage = dict(baseColorMap=texture_path(base_file), baseColorFactor=[1, 1, 1, 1],
                     metallicFactor=float(pbr.get('metallicFactor', 1)), roughnessFactor=float(pbr.get('roughnessFactor', 1)))
        for info_key, channel, field in [('metallicRoughnessTexture', 1, 'roughnessMap'),
                                          ('metallicRoughnessTexture', 2, 'metallicMap'),
                                          ('occlusionTexture', 0, 'ambientOcclusionMap'),
                                          ('normalTexture', None, 'normalMap')]:
            info = pbr.get(info_key) if info_key == 'metallicRoughnessTexture' else source.get(info_key)
            if not info:
                continue
            pixels = texture_image(document, chunks, info)
            pixels = pixels.resize(dimensions(pixels.size, maximum), Image.Resampling.LANCZOS)
            if channel is None:
                pixels = pixels.convert('RGB')  # glTF and BeamNG both use OpenGL tangent-space normals.
                suffix = '.normal.png'
                if info.get('scale', 1) != 1:
                    warnings.append(original_name + ': normal texture scale is not exported')
            else:
                pixels = pixels.getchannel(channel)
                if info_key == 'occlusionTexture':
                    strength = float(info.get('strength', 1))
                    pixels = Image.fromarray(np.rint(255 * (1 - strength) + np.asarray(pixels) * strength).astype(np.uint8))
                suffix = '.data.png'
            filename = name + '_' + field + suffix
            pixels.save(out / 'textures' / filename)
            stage[field] = texture_path(filename)
        definition = dict(name=name, mapTo=name, **{'class': 'Material'}, version=1.5,
                          Stages=[stage, {}, {}, {}], doubleSided=bool(source.get('doubleSided', False)))
        if source.get('alphaMode') == 'MASK':
            definition.update(alphaTest=True, alphaRef=int(round(source.get('alphaCutoff', .5) * 255)))
        elif source.get('alphaMode') == 'BLEND':
            definition.update(translucent=True, translucentBlendOp='LerpAlpha')
        if any(source.get('emissiveFactor', [0, 0, 0])) or source.get('emissiveTexture'):
            warnings.append(original_name + ': emissive material is not exported')
        materials[name] = definition
        records.append(dict(source_name=original_name, engine_name=name))
    return materials, records, material_ids, warnings


def orientation_matrix(tilt_x=0., tilt_y=0., yaw=0.):
    x, y, z = map(math.radians, (tilt_x, tilt_y, yaw))
    rx = np.array([[1, 0, 0], [0, math.cos(x), -math.sin(x)], [0, math.sin(x), math.cos(x)]])
    ry = np.array([[math.cos(y), 0, math.sin(y)], [0, 1, 0], [-math.sin(y), 0, math.cos(y)]])
    rz = np.array([[math.cos(z), -math.sin(z), 0], [math.sin(z), math.cos(z), 0], [0, 0, 1]])
    return rz @ ry @ rx


def transform_geometry(meshes, scale=1., target_height=None, target_width=None, yaw=0., center='base',
                       tilt_x=0., tilt_y=0., origin_xy=None):
    """Orient first, then measure actual vertices and choose an explicit geographic anchor."""
    rotate = orientation_matrix(tilt_x, tilt_y, yaw)
    main = meshes[0]['positions']
    source_bounds = [main.min(0).tolist(), main.max(0).tolist()]
    oriented = main @ rotate.T
    low, high = oriented.min(axis=0), oriented.max(axis=0)
    extent = high - low
    if target_height is not None:
        if extent[2] <= 0:
            raise ValueError('Cannot set height of a zero-height model')
        scale = target_height / extent[2]
    elif target_width is not None:
        if max(extent[:2]) <= 0:
            raise ValueError('Cannot set width of a zero-width model')
        scale = target_width / max(extent[:2])
    origin = (low + high) / 2 if center != 'origin' else np.zeros(3)
    if center == 'base':
        origin[2] = low[2]
    if origin_xy is not None:
        if len(origin_xy) != 2 or not np.isfinite(origin_xy).all():
            raise ValueError('origin-xy must contain two finite oriented source coordinates')
        origin[:2] = origin_xy
    for mesh in meshes:
        mesh['positions'] = (mesh['positions'] @ rotate.T - origin) * scale
        mesh['normals'] = mesh['normals'] @ rotate.T
    return dict(source_bounds=source_bounds, oriented_bounds=[low.tolist(), high.tolist()], scale=scale,
                oriented_source_origin=origin.tolist(), source_origin=(origin @ rotate).tolist(),
                tilt_x_degrees=tilt_x, tilt_y_degrees=tilt_y, yaw_degrees=yaw,
                rotation_order='X tilt, Y tilt, Z yaw; then center and uniform scale',
                center=center, target_height=target_height, target_width=target_width)


def validate_mesh(mesh, materials):
    positions, triangles = mesh['positions'], mesh['tris']
    if positions.ndim != 2 or positions.shape[1] != 3 or not 0 < len(positions) <= 65000:
        raise ValueError('Compiled mesh requires 1..65000 vertices')
    if triangles.ndim != 2 or triangles.shape[1] != 3 or not len(triangles) or triangles.min() < 0 or triangles.max() >= len(positions):
        raise ValueError('Invalid triangle indices')
    for key, shape in [('normals', positions.shape), ('uvs', (len(positions), 2))]:
        if mesh[key].shape != shape or not np.isfinite(mesh[key]).all():
            raise ValueError('Invalid ' + key)
    if not np.isfinite(positions).all():
        raise ValueError('Nonfinite geometry')
    tm = mesh['tri_materials']
    if tm.shape != (len(triangles),) or tm.min() < 0 or tm.max() >= len(materials):
        raise ValueError('Invalid per-triangle material indices')


def convert(args):
    source = Path(args.source).resolve()
    document, chunks, digest = read_glb(source)
    if document.get('skins'):
        raise ValueError('Skinned GLBs are not static landmarks')
    metadata = json.loads(Path(args.metadata).read_text(encoding='utf-8-sig')) if args.metadata else {}
    for key in ('creator', 'license', 'source_url'):
        value = getattr(args, key, None) or metadata.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError('Attribution requires ' + key + ' (metadata or CLI)')
        metadata[key] = value.strip()
    if metadata.get('source_sha256') and metadata['source_sha256'] != digest:
        raise ValueError('Source checksum differs from attribution metadata')
    out = Path(args.out).resolve()
    if out.exists() and any(out.iterdir()):
        raise ValueError('Output directory must be empty; use a new directory for a reproducible conversion')
    out.mkdir(parents=True, exist_ok=True)
    (out / 'textures').mkdir()
    virtual = args.virtual_path or '/levels/beamng_earth/art/shapes/beamngEarth/landmarks/' + args.asset_id
    if not virtual.startswith('/') or '..' in PurePosixPath(virtual).parts:
        raise ValueError('virtual-path must be an absolute BeamNG resource path without ..')
    uv_warnings = normalize_duplicate_uvs(document, chunks)
    definitions, records, material_ids, warnings = material_assets(document, chunks, out, virtual, args.asset_id, args.max_texture)
    warnings[:0] = uv_warnings
    for index, material in enumerate(document.get('materials', [])):
        material['name'] = records[index]['engine_name']
    # Assign the generated default explicitly so Blender never invents a material mapping.
    default_index = len(document.get('materials', []))
    document.setdefault('materials', []).append({'name': records[-1]['engine_name']})
    for mesh in document.get('meshes', []):
        for primitive in mesh['primitives']:
            primitive.setdefault('material', default_index)
            if 'COLOR_0' in primitive.get('attributes', {}):
                raise ValueError('Vertex-colored GLBs need baking before conversion')
    with tempfile.TemporaryDirectory(prefix='bnge-landmark-') as temporary:
        temp = Path(temporary)
        prepared = temp / 'input.glb'
        write_glb(prepared, document, chunks)
        config = dict(source=str(prepared), output=str(temp), material_ids=material_ids,
                      max_triangles=args.max_triangles, far_triangles=args.far_triangles, far_ratio=args.far_ratio,
                      preserve_simple_max=getattr(args, 'preserve_simple_max', 128))
        crop_min, crop_max = getattr(args, 'crop_min', None), getattr(args, 'crop_max', None)
        if crop_min is not None or crop_max is not None:
            if crop_min is None or crop_max is None or not np.isfinite(crop_min + crop_max).all() or np.any(np.asarray(crop_min) >= crop_max):
                raise ValueError('crop-min and crop-max must define a finite, positive-volume oriented box')
            config.update(crop_min=crop_min, crop_max=crop_max, crop_rotation=orientation_matrix(
                getattr(args, 'tilt_x', 0.), getattr(args, 'tilt_y', 0.), args.yaw).tolist())
        (temp / 'config.json').write_text(json.dumps(config), encoding='utf-8')
        command = [args.blender, '--background', '--factory-startup', '--disable-autoexec', '--python-exit-code', '1',
                   '--python', str(Path(__file__).with_name('blender_landmark.py')), '--', '--config', str(temp / 'config.json')]
        result = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
        if result.returncode:
            (out / 'conversion.log').write_text(result.stdout + result.stderr, encoding='utf-8')
            raise RuntimeError('Blender conversion failed; see ' + str(out / 'conversion.log'))
        meshes = []
        for level in ('main', 'far'):
            with np.load(temp / (level + '.npz'), allow_pickle=False) as bundle:
                meshes.append({name: bundle[name].copy() for name in bundle.files})
        geometry = json.loads((temp / 'geometry.json').read_text(encoding='utf-8'))
    transform = transform_geometry(meshes, args.scale, args.target_height, args.target_width, args.yaw, args.center,
                                   getattr(args, 'tilt_x', 0.), getattr(args, 'tilt_y', 0.), getattr(args, 'origin_xy', None))
    if getattr(args, 'dimensions_source', None):
        transform['dimensions_source'] = args.dimensions_source
    if crop_min is not None:
        transform['crop_oriented_bounds'] = [crop_min, crop_max]
        warnings.append('Source geometry clipped to the recorded oriented box before decimation; cut surfaces are not capped')
    names = [record['engine_name'] for record in records]
    for mesh in meshes:
        validate_mesh(mesh, names)
    sys.path.insert(0, str(ROOT / 'server'))
    import cdae
    main, far = meshes
    cdae.write(str(out / 'compiled.cdae'), **main, material=names, object_name='bne_lm_' + args.asset_id,
               lods=[(args.lod_pixels, far)])
    used = set(int(i) for mesh in meshes for i in mesh['tri_materials'])
    definitions = {name: value for name, value in definitions.items() if names.index(name) in used}
    referenced = {PurePosixPath(value).name for material in definitions.values() for stage in material['Stages']
                  for key, value in stage.items() if key.endswith('Map') and isinstance(value, str)}
    for texture in (out / 'textures').iterdir():
        if texture.name not in referenced:
            texture.unlink()
    textures = []
    if getattr(args, 'texture_format', 'dds') == 'dds':
        from landmark_textures import compile_textures
        textures = compile_textures(out / 'textures', getattr(args, 'texture_source_out', None))
    (out / 'main.materials.json').write_text(json.dumps(definitions, indent=2) + '\n', encoding='utf-8')
    attribution = {key: metadata[key] for key in ('uid', 'title', 'creator', 'creator_url', 'license', 'license_url', 'source_url') if key in metadata}
    manifest = dict(asset_id=args.asset_id, attribution=attribution, source_sha256=digest, geometry=geometry,
                    transform=transform, bounds=[main['positions'].min(0).tolist(), main['positions'].max(0).tolist()],
                    materials=[record for i, record in enumerate(records) if i in used],
                    textures=textures, texture_format=getattr(args, 'texture_format', 'dds'),
                    virtual_path=virtual, lod_pixels=args.lod_pixels, warnings=warnings,
                    source_units='glTF metres; geographic dimensions must be verified separately',
                    modified='Converted to Z-up static compiled shape, centered/scaled/rotated as recorded; camera LOD decimated; textures converted')
    derivation = {key: metadata[key] for key in ('original_source_sha256', 'modified', 'dimensions_source', 'dimension_sources', 'repair')
                  if key in metadata}
    if derivation:
        manifest['source_derivation'] = derivation
    (out / 'landmark.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('source')
    parser.add_argument('--out', required=True)
    parser.add_argument('--asset-id', required=True)
    parser.add_argument('--metadata')
    parser.add_argument('--creator')
    parser.add_argument('--license')
    parser.add_argument('--source-url')
    parser.add_argument('--blender', default=os.environ.get('BLENDER_EXE') or shutil.which('blender') or
                        'C:/Program Files/Blender Foundation/Blender 5.2/blender.exe')
    size = parser.add_mutually_exclusive_group()
    size.add_argument('--scale', type=float, default=1.)
    size.add_argument('--target-height', '--height', type=float)
    size.add_argument('--target-width', '--width', type=float, help='Scale the largest horizontal source extent to this many metres')
    parser.add_argument('--yaw', type=float, default=0.)
    parser.add_argument('--tilt-x', type=float, default=0.)
    parser.add_argument('--tilt-y', type=float, default=0.)
    parser.add_argument('--origin-xy', type=float, nargs=2, help='Geographic anchor in oriented source coordinates before scaling')
    parser.add_argument('--crop-min', type=float, nargs=3, help='Minimum oriented source XYZ retained before decimation')
    parser.add_argument('--crop-max', type=float, nargs=3, help='Maximum oriented source XYZ retained before decimation')
    parser.add_argument('--dimensions-source', help='Reference URL for explicitly supplied real-world dimensions')
    parser.add_argument('--center', choices=('base', 'bounds', 'origin'), default='base')
    parser.add_argument('--virtual-path')
    parser.add_argument('--max-triangles', type=int, default=40000)
    parser.add_argument('--far-triangles', type=int, default=5000)
    parser.add_argument('--far-ratio', type=float, default=.25)
    parser.add_argument('--preserve-simple-max', type=int, default=128, help='Keep source objects at or below this triangle count intact in the far LOD; 0 disables')
    parser.add_argument('--lod-pixels', type=float, default=300.)
    parser.add_argument('--max-texture', type=int, default=2048)
    parser.add_argument('--texture-format', choices=('dds', 'png'), default='dds', help='DDS avoids first-load texture cooking; PNG is for authoring only')
    parser.add_argument('--texture-source-out', help='Optional external folder retaining PNG sources for visual QA')
    args = parser.parse_args()
    if not re.fullmatch(r'[a-z0-9_]{1,48}', args.asset_id):
        parser.error('asset-id must be 1..48 lowercase letters, digits or underscores')
    for value in (args.scale, args.target_height, args.target_width):
        if value is not None and (not math.isfinite(value) or value <= 0):
            parser.error('Scale and target dimensions must be finite and positive')
    if not all(math.isfinite(v) for v in (args.yaw, args.tilt_x, args.tilt_y)) or not 0 < args.far_ratio < 1 or args.max_triangles < 2 or args.far_triangles < 1:
        parser.error('Invalid orientation or triangle/LOD budget')
    if not math.isfinite(args.lod_pixels) or args.lod_pixels <= 2:
        parser.error('lod-pixels must exceed the engine minimum of2px')
    if args.max_texture < 16 or args.max_texture & (args.max_texture - 1):
        parser.error('max-texture must be a power of two, at least16')
    if args.preserve_simple_max < 0:
        parser.error('preserve-simple-max must be zero or positive')
    try:
        manifest = convert(args)
    except (ValueError, OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        parser.exit(1, str(error) + '\n')
    print(json.dumps(manifest, indent=2))


if __name__ == '__main__':
    main()
