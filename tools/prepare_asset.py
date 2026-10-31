"""Prepare admitted local glTF into bounded, provenance-bearing Collada catalog artifacts."""
import argparse
import copy
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from xml.sax.saxutils import escape

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import assetcatalog as ac
import dae


def write_dae(path, geometry, materials):
    """Reuse the existing Collada streams, with one geometry binding per material."""
    import xml.etree.ElementTree as ET
    ns = '{http://www.collada.org/2005/11/COLLADASchema}'
    records = []
    for index, material in enumerate(materials):
        selected = geometry['tris'][geometry['tri_materials'] == index]
        if not len(selected):
            continue
        component = path.parent / (path.stem + f'.material{index}.dae')
        dae.write_grid_dae(str(component), geometry['positions'].ravel(), geometry['uvs'].ravel(), selected.ravel(),
                           material=material['map_to'], normals=geometry['normals'].ravel())
        tree = ET.parse(component)
        root = tree.getroot()
        for node in root.iter():
            for key in ('id', 'source', 'url'):
                value = node.get(key)
                if value and ('g0' in value or value in ('tile', 'scene', '#scene')):
                    node.set(key, value.replace('g0', f'g{index}').replace('tile', f'mesh{index}'))
        records.append(root)
        component.unlink()
    if not records:
        raise ValueError('LOD has no assigned triangles')
    root = records[0]
    for other in records[1:]:
        for library in ('library_effects', 'library_materials', 'library_geometries'):
            root.find(ns + library).extend(other.find(ns + library))
        root.find(ns + 'library_visual_scenes').find(ns + 'visual_scene').extend(
            other.find(ns + 'library_visual_scenes').find(ns + 'visual_scene'))
    ET.register_namespace('', ns[1:-1])
    ET.ElementTree(root).write(path, encoding='utf-8', xml_declaration=True)


def box_geometry(dimensions, trunk=False):
    x, y, z = dimensions
    if trunk:
        x = y = min(x, y, max(.1, z * .10))
        z *= .75
    p = np.array([[-x/2,-y/2,0],[x/2,-y/2,0],[x/2,y/2,0],[-x/2,y/2,0],
                  [-x/2,-y/2,z],[x/2,-y/2,z],[x/2,y/2,z],[-x/2,y/2,z]], np.float64)
    t = np.array([[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],
                  [1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]], np.int64)
    # Duplicate corners for flat outward normals, as the ordinary mesh extraction does.
    faces = p[t]
    n = np.cross(faces[:,1]-faces[:,0],faces[:,2]-faces[:,0])
    n /= np.linalg.norm(n,axis=1)[:,None]
    return dict(positions=faces.reshape(-1,3), normals=np.repeat(n,3,axis=0), uvs=np.zeros((36,2)),
                tris=np.arange(36).reshape(-1,3), tri_materials=np.zeros(12,np.int64))


def _tree_digest(source, resources):
    paths = sorted(set([source] + resources))
    return hashlib.sha256(ac.canonical([(p.relative_to(source.parent).as_posix(), ac.sha256(p)) for p in paths])).hexdigest()


def prepare(source, manifest, root, budgets, blender, allow_fixture=False):
    start = time.perf_counter()
    manifest = ac.validate_manifest(manifest, allow_fixture=allow_fixture, budgets=budgets)
    root, source = Path(root), Path(source)
    root.mkdir(parents=True, exist_ok=True)
    profile = budgets['profiles'][manifest['runtime']['budget_profile']]
    if source.suffix.lower() == '.zip':
        digest, size = ac.sha256(source), source.stat().st_size
        if manifest['source'].get('archive_sha256') != digest or manifest['source'].get('archive_bytes') != size:
            raise ValueError('archive does not match admitted source provenance')
        original = root / 'originals' / (digest + '.zip')
        original.parent.mkdir(parents=True, exist_ok=True)
        if not original.exists():
            shutil.copyfile(source, original)
        elif ac.sha256(original) != digest:
            raise ValueError('preserved original checksum mismatch')
        extracted = root / 'extracted' / digest
        receipt = root / 'extracted' / (digest + '.receipt.json')
        if not extracted.exists():
            ac.safe_extract(original, extracted, budgets['acquisition'])
            ac.atomic_json(receipt, {p.relative_to(extracted).as_posix(): ac.sha256(p)
                                    for p in sorted(extracted.rglob('*')) if p.is_file()})
        else:
            if not receipt.is_file():
                raise ValueError('partial extraction has no validated receipt')
            expected = json.loads(receipt.read_text(encoding='utf-8'))
            actual = {p.relative_to(extracted).as_posix(): ac.sha256(p)
                      for p in sorted(extracted.rglob('*')) if p.is_file() and not p.is_symlink()}
            if expected != actual or any(p.is_symlink() for p in extracted.rglob('*')):
                raise ValueError('extracted original differs from validated archive snapshot')
        gltfs = list(extracted.rglob('*.gltf'))
        if len(gltfs) != 1:
            raise ValueError('archive must contain one unambiguous glTF scene')
        source = gltfs[0]
    data, resources = ac.validate_gltf(source)
    input_digest = _tree_digest(source, resources)
    # Direct local licensed inputs preserve their exact referenced files separately.
    if not manifest['source'].get('archive_sha256'):
        original = root / 'originals' / input_digest
        original.mkdir(parents=True, exist_ok=True)
        for item in [source] + resources:
            dest = original / item.relative_to(source.parent)
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists() and ac.sha256(dest) != ac.sha256(item):
                raise ValueError('preserved local original differs')
            if not dest.exists():
                shutil.copyfile(item, dest)
        manifest['source'].update(archive_sha256=input_digest, archive_bytes=sum(p.stat().st_size for p in set([source]+resources)),
                                  downloaded_at=ac.utcnow())
        source = original / source.name
    key = hashlib.sha256(ac.canonical({'source': manifest['source']['archive_sha256'], 'resources': input_digest,
                                     'recipe': ac.RECIPE, 'profile': profile, 'script': ac.sha256(Path(__file__).with_name('blender_asset.py'))})).hexdigest()
    workspace = root / 'work' / key
    workspace.mkdir(parents=True, exist_ok=False)
    cfg = {'source': str(source.resolve()), 'output': str(workspace.resolve()), 'profile': profile,
           'material_inputs': data.get('materials', [])}
    ac.atomic_json(workspace / 'config.json', cfg)
    environment = {k:v for k,v in os.environ.items() if not any(secret in k.upper() for secret in ('TOKEN','PASSWORD','SECRET','CREDENTIAL'))}
    environment['TEMP'] = environment['TMP'] = str(root / 'tmp')
    Path(environment['TEMP']).mkdir(exist_ok=True)
    result = subprocess.run([str(blender), '--background', '--factory-startup', '--disable-autoexec', '--threads', '1',
                             '--python-exit-code', '2', '--python', str(Path(__file__).with_name('blender_asset.py')),
                             '--', '--config', str(workspace / 'config.json')], env=environment, capture_output=True,
                            text=True, timeout=300)
    (workspace / 'blender.log').write_text(result.stdout + result.stderr, encoding='utf-8')
    if result.returncode:
        raise ValueError('isolated Blender preparation failed; inspect local sanitized work log')
    report = json.loads((workspace / 'geometry.json').read_text(encoding='utf-8'))
    output = root / 'prepared' / key
    output.mkdir(parents=True, exist_ok=False)
    materials, textures, definitions, warnings = [], [], {}, []
    for index, record in enumerate(report['materials']):
        name = f'be2_{manifest["asset_id"]}_{index}'
        material = {'name': record['name'], 'map_to': name, 'base_color': record['base_color']}
        definition = {'name': name, 'class': 'Material', 'mapTo': name, 'version': 1.5, 'Stages': [
            {'baseColorFactor': record['base_color'], 'roughnessFactor': .8, 'metallicFactor': 0}]}
        image = record.get('base_color_texture')
        if image:
            image = Path(image).resolve()
            if not image.is_relative_to(source.parent.resolve()) or not image.is_file():
                raise ValueError('imported texture escapes admitted source')
            target = output / f'texture{index}.png'
            with Image.open(image) as picture:
                if picture.width * picture.height > 67_108_864:
                    raise ValueError('source texture pixel budget exceeded')
                picture.load()
                picture = picture.convert('RGBA')
                picture.thumbnail((profile['texture_max_dimension'],) * 2, Image.Resampling.LANCZOS)
                picture.save(target, format='PNG', optimize=False)
                width, height = picture.size
            definition['Stages'][0]['baseColorMap'] = target.name
            texture = dict(artifact=target.name, sha256=ac.sha256(target), bytes=target.stat().st_size,
                           width=width, height=height, format='png')
            textures.append(texture)
        if record.get('alpha_mode') == 'MASK':
            definition.update(alphaTest=True, alphaRef=128, translucent=False)
        definition['doubleSided'] = record['double_sided']
        definitions[name] = definition
        materials.append(material)
        warnings.extend(record['warnings'])
    if sum(t['bytes'] for t in textures) > profile['texture_bytes']:
        raise ValueError('prepared texture byte budget exceeded')
    ac.atomic_json(output / 'main.materials.json', definitions)
    material_file = output / 'main.materials.json'
    for material in materials:
        material.update(artifact=material_file.name, sha256=ac.sha256(material_file), bytes=material_file.stat().st_size)
    lods = []
    for index in range(3):
        with np.load(workspace / f'lod{index}.npz', allow_pickle=False) as arrays:
            geometry = {name: arrays[name] for name in arrays.files}
        path = output / f'lod{index}.dae'
        write_dae(path, geometry, materials)
        lods.append(dict(artifact=path.name, sha256=ac.sha256(path), bytes=path.stat().st_size,
                         triangles=len(geometry['tris']), vertices=len(geometry['positions'])))
    collision = dict(mode='none', artifact=None, triangles=0)
    if profile['collision_mode'] != 'none':
        geometry = box_geometry(report['dimensions_m'], trunk=profile['collision_mode']=='simple_trunk')
        path = output / 'collision.dae'
        write_dae(path, geometry, materials[:1])
        collision = dict(mode='simple_trunk_box' if profile['collision_mode']=='simple_trunk' else 'simple_box',
                         artifact=path.name, sha256=ac.sha256(path), bytes=path.stat().st_size, triangles=12,
                         engine_binding='pending; separate proxy is not automatically collidable')
    output_files = sorted(p for p in output.iterdir() if p.is_file())
    output_hash = hashlib.sha256(ac.canonical([(p.name, ac.sha256(p)) for p in output_files])).hexdigest()
    manifest['state'] = 'prepared_engine_pending'
    manifest['geometry'].update(source_units='meters (glTF 2.0)', source_up_axis='+Y', units='meters', up_axis='+Z',
        forward_axis='+Y', handedness='right', pivot='ground_center', dimensions_m=report['dimensions_m'], normalization_version=ac.RECIPE)
    manifest['runtime'].update(format='collada', artifact_root=output.relative_to(root).as_posix(), lods=lods,
                               materials=materials, textures=textures, collision=collision, approved=True)
    manifest['quality'].update(geometry_validated=True, textures_validated=True, engine_validated=False,
                              warnings=sorted(set(warnings + ['separate LOD/collision artifacts require engine placement and validation'])))
    manifest['processing'].update(recipe_version=ac.RECIPE, prepared_at=ac.utcnow(), output_sha256=output_hash,
        tool_versions={'blender': report['blender_version'], 'blender_build_hash': report['blender_build_hash'],
                       'python': sys.version.split()[0], 'numpy': np.__version__, 'Pillow': importlib.metadata.version('Pillow')},
        changes=['glTF axis conversion by installed Blender; applied world transforms and mirrored winding correction',
                 'translated ground-center pivot', 'three budgeted independently authored DAE LOD artifacts',
                 'simple bounded collision proxy where required; engine binding pending',
                 'base-color PNG resize/simple PBR material recipe; map reductions recorded in quality warnings'])
    ac.validate_manifest(manifest, prepared=True, allow_fixture=allow_fixture, budgets=budgets)
    ac.verify_outputs(root, manifest, budgets)
    ac.atomic_json(output / 'manifest.json', manifest)
    (output / 'ATTRIBUTION.md').write_text(ac.attribution([manifest]), encoding='utf-8')
    timings = {'wall_seconds': time.perf_counter()-start, 'output': str(output), 'input_sha256': input_digest,
               'source_triangles': report['source_triangles'], 'output_bytes': sum(p.stat().st_size for p in output.iterdir()),
               'engine_validated': False, 'fixture_only': manifest['runtime']['fixture_only'], 'manifest': manifest}
    ac.atomic_json(workspace / 'result.json', timings)
    return timings


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--budgets', type=Path, required=True)
    parser.add_argument('--blender', type=Path, required=True)
    parser.add_argument('--allow-fixture', action='store_true')
    args = parser.parse_args(argv)
    state = (Path(os.environ['LOCALAPPDATA']) / 'beamng-earth-2').resolve()
    if not args.root.resolve().is_relative_to(state):
        parser.error('preparation output must remain in dedicated state')
    try:
        result = prepare(args.source, json.loads(args.manifest.read_text(encoding='utf-8')), args.root,
                         json.loads(args.budgets.read_text(encoding='utf-8')), args.blender, args.allow_fixture)
        print(json.dumps({k:v for k,v in result.items() if k != 'manifest'}, sort_keys=True))
        return 0
    except (ValueError, OSError, subprocess.SubprocessError):
        print(json.dumps({'status': 'rejected', 'reason': 'local admission/conversion/output validation failed'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
