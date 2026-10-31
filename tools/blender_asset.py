"""Trusted background-only glTF geometry recipe; never executes archive scripts."""
import argparse
import json
import math
from pathlib import Path
import sys

import bpy
import numpy as np
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_landmark import extract, fit


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    cfg = json.loads(args.config.read_text(encoding='utf-8'))
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.import_scene.gltf(filepath=cfg['source'], import_pack_images=False)
    objects = sorted((o for o in bpy.context.scene.objects if o.type == 'MESH'), key=lambda o: o.name)
    if not objects or len(objects) > 256:
        raise ValueError('empty or oversized static scene')
    tint = np.ones(4)
    observed_tint = None
    for obj in objects:
        for layer in obj.data.color_attributes:
            colors = np.array([tuple(item.color) for item in layer.data], dtype=np.float64)
            if len(colors):
                if not np.allclose(colors, colors[0], atol=1e-6, rtol=0):
                    raise ValueError('nonuniform vertex colors require a supported bake recipe')
                if observed_tint is not None and not np.allclose(observed_tint, colors[0], atol=1e-6, rtol=0):
                    raise ValueError('per-object vertex tint requires a supported bake recipe')
                observed_tint = colors[0]
    if observed_tint is not None:
        tint = observed_tint
    names = sorted({m.name for obj in objects for m in obj.data.materials if m})
    if len(names) > cfg['profile']['material_slots']:
        raise ValueError('material slot budget exceeded; explicit atlas recipe required')
    material_ids = {name: i for i, name in enumerate(names)}
    if not names:
        names = ['__default__']
    material_ids.setdefault('__default__', 0)
    source = extract(objects, material_ids, 1.)
    positions = source['positions']
    if not np.isfinite(positions).all() or not len(positions):
        raise ValueError('non-finite or empty source geometry')
    minimum, maximum = positions.min(axis=0), positions.max(axis=0)
    dimensions = maximum - minimum
    if np.any(dimensions <= 1e-6) or np.any(dimensions > 10000):
        raise ValueError('physical dimensions outside generic-asset scope')
    pivot = np.array([(minimum[0] + maximum[0]) / 2, (minimum[1] + maximum[1]) / 2, minimum[2]])
    for obj in objects:
        obj.matrix_world = Matrix.Translation(tuple(-pivot)) @ obj.matrix_world
    output = Path(cfg['output'])
    source_count = len(source['tris'])
    counts = []
    previous = source_count
    for index, maximum_count in enumerate(cfg['profile']['lod_triangles']):
        target = min(maximum_count, source_count if index == 0 else max(4, math.floor(previous * .5)))
        geometry = extract(objects, material_ids, 1.) if source_count <= target and len(positions) <= 65000 else fit(
            objects, material_ids, target, source_count)
        if not all(np.isfinite(geometry[k]).all() for k in ('positions', 'normals', 'uvs')):
            raise ValueError('non-finite prepared geometry')
        faces = geometry['positions'][geometry['tris']]
        cross = np.cross(faces[:, 1] - faces[:, 0], faces[:, 2] - faces[:, 0])
        if np.any(np.linalg.norm(cross, axis=1) < 1e-12):
            raise ValueError('degenerate prepared triangles')
        normals = geometry['normals'][geometry['tris']].mean(axis=1)
        if np.any(np.sum(cross * normals, axis=1) < -1e-8):
            raise ValueError('winding and transformed normals disagree')
        np.savez_compressed(output / f'lod{index}.npz', **geometry)
        counts.append({'triangles': len(geometry['tris']), 'vertices': len(geometry['positions'])})
        previous = len(geometry['tris'])
    materials = []
    for name in names:
        material = bpy.data.materials.get(name)
        record = {'name': name, 'base_color': [0.6, 0.6, 0.6, 1.0], 'base_color_texture': None,
                  'alpha_mode': 'OPAQUE', 'double_sided': False, 'warnings': []}
        if material:
            record['base_color'] = list(material.diffuse_color)
            record['double_sided'] = not material.use_backface_culling
            if material.use_nodes:
                principled = next((n for n in material.node_tree.nodes if n.type == 'BSDF_PRINCIPLED'), None)
                if principled:
                    color = principled.inputs.get('Base Color')
                    record['base_color'] = list(color.default_value)
                    visited, images = set(), []
                    def upstream(socket):
                        for link in socket.links:
                            node = link.from_node
                            if node.name in visited:
                                continue
                            visited.add(node.name)
                            if node.type == 'TEX_IMAGE' and node.image:
                                images.append(node.image)
                            else:
                                for candidate in node.inputs:
                                    upstream(candidate)
                    upstream(color)
                    if len({image.filepath for image in images}) > 1:
                        raise ValueError('multiple base-color images require explicit baking')
                    if images:
                        image = images[0]
                        if image.packed_file:
                            raise ValueError('packed images require explicit local-resource recipe')
                        record['base_color_texture'] = bpy.path.abspath(image.filepath)
                    elif color.is_linked:
                        raise ValueError('unsupported procedural base color')
                    if principled.inputs.get('Normal').is_linked:
                        record['warnings'].append('normal map intentionally omitted by simple-material recipe')
                    if principled.inputs.get('Roughness').is_linked or principled.inputs.get('Metallic').is_linked:
                        record['warnings'].append('roughness/metallic textures intentionally reduced to simple-material defaults')
                    alpha = principled.inputs.get('Alpha')
                    if alpha.is_linked or alpha.default_value < 1:
                        record['alpha_mode'] = 'MASK'
                    if principled.inputs.get('Emission Color').is_linked:
                        record['warnings'].append('emissive texture intentionally omitted by simple-material recipe')
        source_materials = cfg.get('material_inputs', [])
        source_material = next((m for m in source_materials if m.get('name') == name), None)
        if source_material is None and len(source_materials) == 1:
            source_material = source_materials[0]
        if source_material is not None:
            pbr = source_material.get('pbrMetallicRoughness', {})
            extension = source_material.get('extensions', {}).get('KHR_materials_pbrSpecularGlossiness')
            factor = pbr.get('baseColorFactor', [1, 1, 1, 1]) if extension is None else extension.get('diffuseFactor', [1, 1, 1, 1])
            record['base_color'] = (np.array(factor) * tint).tolist()
            if extension is not None:
                record['warnings'].append('KHR_materials_pbrSpecularGlossiness imported; specular/gloss map reduced by simple-material recipe')
            record['alpha_mode'] = source_material.get('alphaMode', 'OPAQUE')
        if observed_tint is not None:
            record['warnings'].append('uniform vertex color preserved as material base-color multiplier')
        materials.append(record)
    (output / 'geometry.json').write_text(json.dumps({
        'blender_version': bpy.app.version_string, 'blender_build_hash': bpy.app.build_hash.decode(),
        'source_triangles': source_count, 'lods': counts, 'dimensions_m': dimensions.tolist(),
        'source_ground_center': pivot.tolist(), 'materials': materials,
        'coordinate_policy': 'glTF meters/+Y-up imported by Blender; world transforms applied; right-handed +Z-up; ground-center translated',
        'winding_policy': 'inverse-transpose normals and mirrored determinant winding correction; finite/degenerate/normal agreement checks',
    }, sort_keys=True, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
