"""Blender-only geometry extraction helper for convert_landmark.py (not a standalone importer)."""
import argparse
import json
import math
from pathlib import Path
import sys

import bpy
import bmesh
import numpy as np
from mathutils import Matrix, Vector


def crop_objects(objects, cfg):
    """Clip in oriented world space; Blender interpolates UVs at new boundary vertices."""
    rotate = Matrix(cfg['crop_rotation']).to_4x4()
    result = []
    for obj in objects:
        mesh = bmesh.new()
        mesh.from_mesh(obj.data)
        mesh.transform(rotate @ obj.matrix_world)
        for axis in range(3):
            for bound, direction in ((cfg['crop_min'][axis], -1), (cfg['crop_max'][axis], 1)):
                point, normal = Vector((0., 0., 0.)), Vector((0., 0., 0.))
                point[axis], normal[axis] = bound, direction
                bmesh.ops.bisect_plane(mesh, geom=list(mesh.verts) + list(mesh.edges) + list(mesh.faces),
                                      dist=1e-6, plane_co=point, plane_no=normal, clear_outer=True, clear_inner=False)
        mesh.transform(rotate.inverted())
        mesh.normal_update()
        mesh.to_mesh(obj.data)
        mesh.free()
        obj.matrix_world = Matrix.Identity(4)
        if len(obj.data.polygons):
            result.append(obj)
    if not result:
        raise ValueError('The crop box removed the entire landmark')
    return result


def extract(objects, material_ids, ratio, preserve_simple_max=0):
    """Keep distinct corner normals/UVs; an indexed position alone cannot preserve hard edges."""
    vertices, normals, uvs, triangles, tri_materials = [], [], [], [], []
    unique = {}
    for original in objects:
        obj = original.copy()
        obj.data = original.data.copy()
        bpy.context.collection.objects.link(obj)
        source_triangles = sum(len(face.vertices) - 2 for face in original.data.polygons)
        if ratio < 1 and source_triangles > preserve_simple_max:
            modifier = obj.modifiers.new('landmark_lod', 'DECIMATE')
            modifier.ratio = ratio
            modifier.use_collapse_triangulate = True
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=bpy.context.evaluated_depsgraph_get())
        mesh.calc_loop_triangles()
        world = obj.matrix_world
        normal_matrix = world.to_3x3().inverted_safe().transposed()
        mirrored = world.to_3x3().determinant() < 0
        uv_layer = mesh.uv_layers.active
        corner_normals = mesh.corner_normals
        for triangle in mesh.loop_triangles:
            indices = []
            for loop_id in triangle.loops:
                loop = mesh.loops[loop_id]
                position = tuple(world @ mesh.vertices[loop.vertex_index].co)
                normal = tuple((normal_matrix @ corner_normals[loop_id].vector).normalized())
                uv = tuple(uv_layer.data[loop_id].uv) if uv_layer else (0., 0.)
                key = position + normal + uv
                index = unique.get(key)
                if index is None:
                    index = len(vertices)
                    unique[key] = index
                    vertices.append(position)
                    normals.append(normal)
                    uvs.append(uv)
                indices.append(index)
            if mirrored:
                indices.reverse()
            triangles.append(indices)
            mat = mesh.materials[triangle.material_index] if mesh.materials else None
            tri_materials.append(material_ids[mat.name] if mat else material_ids['__default__'])
        evaluated.to_mesh_clear()
        mesh_data = obj.data
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh_data)
    return dict(positions=np.asarray(vertices, np.float64), normals=np.asarray(normals, np.float64),
                uvs=np.asarray(uvs, np.float64), tris=np.asarray(triangles, np.int64),
                tri_materials=np.asarray(tri_materials, np.int64))


def fit(objects, material_ids, target, original_triangles, start_ratio=1., preserve_simple_max=0):
    ratio = min(start_ratio, target / original_triangles)
    for _ in range(12):
        data = extract(objects, material_ids, ratio, preserve_simple_max)
        nt, nv = len(data['tris']), len(data['positions'])
        if 0 < nt <= target and nv <= 65000:
            return data
        if nt == 0:
            raise ValueError('Decimation removed the entire landmark')
        ratio *= min(target / nt, 65000 / max(nv, 1), .9) * .95
    raise ValueError('Cannot meet triangle/65000-vertex budget without losing the landmark')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    cfg = json.loads(Path(args.config).read_text(encoding='utf-8'))
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.import_scene.gltf(filepath=cfg['source'])
    objects = sorted((o for o in bpy.context.scene.objects if o.type == 'MESH'), key=lambda o: o.name)
    if not objects:
        raise ValueError('The source contains no mesh objects')
    if 'crop_min' in cfg:
        objects = crop_objects(objects, cfg)
    source = extract(objects, cfg['material_ids'], 1.)
    count = len(source['tris'])
    if count < 2:
        raise ValueError('At least two triangles are required for a reduced camera LOD')
    main_mesh = source if count <= cfg['max_triangles'] and len(source['positions']) <= 65000 else fit(
        objects, cfg['material_ids'], cfg['max_triangles'], count)
    far_target = min(cfg['far_triangles'], max(1, math.floor(len(main_mesh['tris']) * cfg['far_ratio'])))
    preserved = sum(n for obj in objects if (n := sum(len(p.vertices) - 2 for p in obj.data.polygons)) <= cfg['preserve_simple_max'])
    if preserved >= far_target:
        raise ValueError('The far triangle budget is below the preserved simple geometry; raise it or lower preserve-simple-max')
    far = fit(objects, cfg['material_ids'], far_target, count, preserve_simple_max=cfg['preserve_simple_max'])
    if len(far['tris']) >= len(main_mesh['tris']):
        raise ValueError('The far LOD must contain fewer triangles than the main mesh')
    for name, data in [('main', main_mesh), ('far', far)]:
        np.savez_compressed(Path(cfg['output']) / (name + '.npz'), **data)
    Path(cfg['output'], 'geometry.json').write_text(json.dumps({
        'blender_version': bpy.app.version_string, 'source_triangles': count,
        'main_triangles': len(main_mesh['tris']), 'main_vertices': len(main_mesh['positions']),
        'far_triangles': len(far['tris']), 'far_vertices': len(far['positions']),
    }, indent=2), encoding='utf-8')


if __name__ == '__main__':
    main()
