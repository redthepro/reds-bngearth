"""Offline, asset-specific Washington Monument correction; run inside Blender.

blender --background --factory-startup --disable-autoexec --python-exit-code 1 \
  --python tools/repair_washington.py -- --source source.glb \
  --metadata source-metadata.json --output build/washington-repaired --render

The licensed plinth and its UVs/materials are retained. The source's narrow,
untapered shaft and fictional open chamber are replaced with a closed obelisk.
This script only accepts the reviewed original asset, identified by SHA-256.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import sys

import bpy
import bmesh
from mathutils import Matrix, Vector


ORIGINAL_SHA256 = '6c5293cd178066394add306a44a96c17ae58c1d5440d4d0eedd18eb5dda0b09d'
APEX_M = 169.294175       # historic 555 ft 5 1/8 in datum, not the modern entrance datum
BASE_WIDTH_M = 16.8021   # 55 ft 1 1/2 in
TOP_WIDTH_M = 10.5029    # 34 ft 5 1/2 in at the pyramidion base
PYRAMID_BASE_M = 152.4   # NPS's stated 500-foot level
DIMENSION_SOURCES = [
    'https://www.nps.gov/wamo/learn/historyculture/index.htm',
    'https://npgallery.nps.gov/pdfhost/docs/NRHP/Text/66000035.pdf',
    'https://www.ngs.noaa.gov/surveys/ngs/wm2013/',
]


def bounds(objects):
    points = [obj.matrix_world @ v.co for obj in objects for v in obj.data.vertices]
    return ([min(p[i] for p in points) for i in range(3)],
            [max(p[i] for p in points) for i in range(3)])


def stats(obj):
    mesh = bmesh.new()
    mesh.from_mesh(obj.data)
    result = dict(name=obj.name, vertices=len(mesh.verts),
                  triangles=sum(len(face.verts) - 2 for face in mesh.faces),
                  boundary_edges=sum(edge.is_boundary for edge in mesh.edges),
                  nonmanifold_edges=sum(not edge.is_manifold for edge in mesh.edges),
                  signed_volume_m3=mesh.calc_volume(signed=True), bounds=bounds([obj]))
    mesh.free()
    return result


def geometry_digest(obj):
    """Exact positions, topology, material slots and UVs; no numerical volume summation."""
    digest = hashlib.sha256()
    for vertex in obj.data.vertices:
        digest.update(struct.pack('<3f', *vertex.co))
    for polygon in obj.data.polygons:
        digest.update(struct.pack('<2I', polygon.material_index, len(polygon.vertices)))
        digest.update(struct.pack('<' + 'I' * len(polygon.vertices), *polygon.vertices))
    for layer in obj.data.uv_layers:
        for loop in layer.data:
            digest.update(struct.pack('<2f', *loop.uv))
    return digest.hexdigest()


def corrected_shaft(material):
    # One closed manifold: four tapered walls, four pyramid faces, and an underside.
    # Its base starts at the same Z=0 datum as the plinth's lowest point. The plinth
    # intersects the lowest shaft; its thickness does not add to the official height.
    vertices = []
    for z, side in ((0., BASE_WIDTH_M), (PYRAMID_BASE_M, TOP_WIDTH_M)):
        half = side / 2
        vertices.extend([(-half, -half, z), (half, -half, z),
                         (half, half, z), (-half, half, z)])
    vertices.append((0., 0., APEX_M))
    faces = [(3, 2, 1, 0)]
    for side in range(4):
        nxt = (side + 1) % 4
        faces.append((side, nxt, nxt + 4, side + 4))
        faces.append((side + 4, nxt + 4, 8))
    data = bpy.data.meshes.new('Washington_dimensioned_closed_shaft')
    data.from_pydata(vertices, [], faces)
    data.materials.append(material)
    data.update()
    mesh = bmesh.new()
    mesh.from_mesh(data)
    bmesh.ops.recalc_face_normals(mesh, faces=list(mesh.faces))
    mesh.to_mesh(data)
    mesh.free()
    # Use a clean limestone patch from the original Monument atlas. Reusing the
    # original chamber/window islands would paint black openings onto solid walls.
    # Keep flat corner normals so the four shaft and pyramid faces stay crisp.
    uv = data.uv_layers.new(name='UVMap')
    for polygon in data.polygons:
        polygon.use_smooth = False
        coordinates = ((.72, .25), (.88, .25), (.88, .75), (.72, .75))
        if len(polygon.loop_indices) == 3:
            coordinates = ((.72, .25), (.88, .25), (.80, .75))
        for index, coordinate in zip(polygon.loop_indices, coordinates):
            uv.data[index].uv = coordinate
    obj = bpy.data.objects.new('Washington_corrected_shaft', data)
    bpy.context.collection.objects.link(obj)
    audit = stats(obj)
    if audit['boundary_edges'] or audit['nonmanifold_edges'] or audit['signed_volume_m3'] <= 0:
        raise ValueError('The corrected shaft must be closed with outward normals')
    if audit['triangles'] != 14:
        raise ValueError('Unexpected shaft topology')
    return obj


def setup_render():
    scene = bpy.context.scene
    scene.render.engine = 'CYCLES'
    scene.cycles.device = 'CPU'
    scene.cycles.samples = 12
    scene.render.threads_mode = 'FIXED'
    scene.render.threads = 2
    scene.render.resolution_percentage = 100
    scene.world.use_nodes = True
    background = scene.world.node_tree.nodes['Background']
    background.inputs['Color'].default_value = (.4, .47, .56, 1.)
    background.inputs['Strength'].default_value = .65
    bpy.ops.object.light_add(type='SUN', location=(100, -100, 200))
    sun = bpy.context.object
    sun.data.energy = 2.
    sun.rotation_euler = Vector((-.7, .5, -1.)).to_track_quat('-Z', 'Y').to_euler()
    bpy.ops.object.camera_add()
    camera = bpy.context.object
    camera.data.type = 'ORTHO'
    camera.data.clip_end = 2000
    scene.camera = camera
    return scene, camera


def render(scene, camera, directory, label):
    for detail in (False, True):
        target = Vector((0., 0., 154. if detail else 84.))
        camera.location = target + Vector((160., -240., 55. if detail else 110.))
        camera.rotation_euler = (target - camera.location).to_track_quat('-Z', 'Y').to_euler()
        camera.data.ortho_scale = 43. if detail else 188.
        scene.render.resolution_x = 850 if detail else 700
        scene.render.resolution_y = 850 if detail else 1000
        scene.render.filepath = str(directory / (label + ('-tip' if detail else '') + '.png'))
        bpy.ops.render.render(write_still=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--metadata', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--render', action='store_true')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:])
    # Blender resolves relative render paths against its project, not the shell cwd.
    args.source, args.metadata, args.output = (path.resolve() for path in (args.source, args.metadata, args.output))
    digest = hashlib.sha256(args.source.read_bytes()).hexdigest()
    if digest != ORIGINAL_SHA256:
        raise ValueError('This repair is specific to the reviewed original GLB SHA-256')
    metadata = json.loads(args.metadata.read_text(encoding='utf-8'))
    attribution = {key: metadata[key] for key in
                   ('creator', 'creator_url', 'source_url', 'license', 'license_url')}
    if metadata.get('source_sha256') != digest:
        raise ValueError('Source metadata checksum mismatch')
    args.output.mkdir(parents=True, exist_ok=True)
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    bpy.ops.import_scene.gltf(filepath=str(args.source.resolve()))
    objects = [obj for obj in bpy.context.scene.objects if obj.type == 'MESH']
    plinths = [obj for obj in objects if [m.name for m in obj.data.materials] == ['Wall']]
    shafts = [obj for obj in objects if [m.name for m in obj.data.materials] == ['Monument']]
    if len(objects) != 2 or len(plinths) != 1 or len(shafts) != 1:
        raise ValueError('Unexpected source objects/materials')
    plinth, old_shaft = plinths[0], shafts[0]
    original_bounds = bounds(objects)
    scale = APEX_M / (original_bounds[1][2] - original_bounds[0][2])
    transform = Matrix.Scale(scale, 4) @ Matrix.Translation((0., 0., -original_bounds[0][2]))
    for obj in objects:
        obj.data.transform(transform @ obj.matrix_world)
        obj.matrix_world = Matrix.Identity(4)
        obj.data.update()
    before = [stats(obj) for obj in objects]
    plinth_digest = geometry_digest(plinth)
    if args.render:
        scene, camera = setup_render()
        render(scene, camera, args.output, 'before')
    material = old_shaft.data.materials[0]
    bpy.data.objects.remove(old_shaft, do_unlink=True)
    shaft = corrected_shaft(material)
    after = [stats(plinth), stats(shaft)]
    if plinth_digest != geometry_digest(plinth):
        raise ValueError('Plinth geometry was changed by shaft replacement')
    target = args.output / 'source.glb'
    bpy.ops.object.select_all(action='DESELECT')
    plinth.select_set(True)
    shaft.select_set(True)
    bpy.ops.export_scene.gltf(filepath=str(target.resolve()), export_format='GLB',
                              use_selection=True, export_yup=True, export_normals=True,
                              export_texcoords=True, export_materials='EXPORT')
    modified = (
        'Retained the licensed source plinth, its UVs and materials with uniform meter scaling. '
        'Replaced the original 82-triangle untapered, undersized shaft and its fictional open '
        'chamber with a closed 14-triangle tapered obelisk: 16.8021m base side, 10.5029m '
        'pyramid-base side at Z=152.4m, apex Z=169.294175m. Recalculated outward flat normals '
        'and mapped the replacement to a clean limestone patch of the original Monument atlas. '
        'Small observation windows are omitted. Shaft and plinth share Z=0 at the lowest '
        'plinth point; the plinth overlaps the lowest shaft and adds no extra height. '
        'The 500ft junction is an NPS stated level, not a sub-inch survey value.')
    derived = dict(metadata)
    derived.update(attribution)
    derived.update(source_sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                   original_source_sha256=digest, modified=modified,
                   dimensions_source=DIMENSION_SOURCES[0], dimension_sources=DIMENSION_SOURCES,
                   triangles=sum(row['triangles'] for row in after),
                   repair=dict(blender_version=bpy.app.version_string,
                               original_bounds=original_bounds, original_to_meters=scale,
                               before=before, after=after,
                               retained_plinth_geometry_sha256=plinth_digest,
                               height_datum='historic height; shared modeled base Z=0',
                               apex_m=APEX_M, base_width_m=BASE_WIDTH_M,
                               pyramid_base_width_m=TOP_WIDTH_M, pyramid_base_z_m=PYRAMID_BASE_M,
                               preserve_lod_objects=[shaft.name]))
    (args.output / 'source-metadata.json').write_text(json.dumps(derived, indent=2) + '\n', encoding='utf-8')
    if args.render:
        render(scene, camera, args.output, 'after')
    print('WASHINGTON_REPAIRED', json.dumps(dict(path=str(target), triangles=derived['triangles'],
                                                shaft=after[1], source_sha256=derived['source_sha256'])))


if __name__ == '__main__':
    main()
