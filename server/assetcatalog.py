"""Offline asset admission and immutable local catalog. Never fetches at runtime."""
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import re
import stat
from urllib.parse import unquote, urlsplit
import zipfile

import compiledcache
import staging

SCHEMA = 2
RECIPE = 1
MAX_JSON = 1 << 20
SHA = re.compile(r'^[0-9a-f]{64}$')
UID = re.compile(r'^[0-9a-f]{32}$')
IDENT = re.compile(r'^[a-z][a-z0-9_.-]{0,95}$')
LICENSES = {'cc0': 'https://creativecommons.org/publicdomain/zero/1.0/',
            'cc-by': 'https://creativecommons.org/licenses/by/4.0/'}
ALLOWED_SUFFIXES = {'.gltf', '.glb', '.bin', '.png', '.jpg', '.jpeg', '.webp', '.txt', '.md', '.json'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical(value)
    if len(raw) > MAX_JSON:
        raise ValueError('asset JSON exceeds budget')
    temporary = staging.tmp_for(str(path))
    Path(temporary).write_bytes(raw + b'\n')
    staging.replace_cached(temporary, str(path))


def public_url(value):
    if not isinstance(value, str):
        return False
    parsed = urlsplit(value)
    return parsed.scheme == 'https' and bool(parsed.hostname) and not any(
        (parsed.username, parsed.password, parsed.query, parsed.fragment))


def _text(value):
    return isinstance(value, str) and 0 < len(value.strip()) <= 4096


def _timestamp(value):
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).tzinfo is not None
    except (ValueError, AttributeError):
        return False


def _mapping(value, label):
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be an object')
    return value


def license_status(license):
    """Unknown/review licenses never become conversion permission by a boolean alone."""
    if not isinstance(license, dict):
        return 'rejected'
    identifier = license.get('id')
    if identifier in ('cc-by-sa', 'cc-by-nc', 'cc-by-nc-sa'):
        return 'review_required'
    if identifier not in LICENSES or license.get('url') != LICENSES[identifier]:
        return 'rejected'
    if not all(license.get(name) is True for name in ('redistribution_verified', 'derivatives_verified')):
        return 'unverified'
    if not all(_text(license.get(name)) for name in ('label', 'attribution')):
        return 'unverified'
    if not _timestamp(license.get('verified_at')) or not SHA.fullmatch(str(license.get('evidence_sha256', ''))):
        return 'unverified'
    return 'eligible'


def validate_manifest(manifest, prepared=False, allow_fixture=False, budgets=None):
    """Validate admission before acquisition, then all output fields before catalog publication."""
    if not isinstance(manifest, dict) or manifest.get('schema_version') != SCHEMA or type(manifest.get('schema_version')) is not int:
        raise ValueError('unsupported asset manifest schema')
    if manifest.get('example_only', False) is not False or not IDENT.fullmatch(str(manifest.get('asset_id', ''))):
        raise ValueError('example or invalid asset identity')
    source = _mapping(manifest.get('source'), 'source')
    fixture = source.get('provider') == 'local_fixture'
    if fixture and not allow_fixture:
        raise ValueError('fixture is not a distributable catalog asset')
    if source.get('provider') not in ('sketchfab', 'local_fixture'):
        raise ValueError('unsupported asset provider')
    if not UID.fullmatch(str(source.get('model_uid', ''))):
        raise ValueError('model UID is required')
    if not all(_text(source.get(k)) for k in ('model_name', 'creator_name')):
        raise ValueError('model and creator names are required')
    if not all(public_url(source.get(k)) for k in ('model_url', 'creator_url')):
        raise ValueError('public model and creator URLs are required')
    if source.get('provider') == 'sketchfab':
        if urlsplit(source['model_url']).hostname not in ('sketchfab.com', 'www.sketchfab.com') or not source['model_url'].rstrip('/').endswith(source['model_uid']):
            raise ValueError('model URL does not identify the model UID')
        if source.get('api_revision') != 'v3':
            raise ValueError('unverified provider API revision')
    if license_status(manifest.get('license')) != 'eligible':
        raise ValueError('license is not verified CC0 or CC BY 4.0')
    semantic = _mapping(manifest.get('semantic'), 'semantic')
    if semantic.get('placement_mode') != 'generic' or semantic.get('verified_location') is not None:
        raise ValueError('this recipe admits generic local enrichment only')
    if semantic.get('fallback') != 'procedural':
        raise ValueError('procedural fallback must remain available')
    for field in ('categories', 'regions', 'climate_types'):
        values = semantic.get(field)
        if not isinstance(values, list) or len(values) > 32 or any(not _text(v) for v in values):
            raise ValueError('invalid semantic applicability')
    if not semantic['categories']:
        raise ValueError('asset category is required')
    runtime = _mapping(manifest.get('runtime'), 'runtime')
    if type(runtime.get('fixture_only')) is not bool or runtime['fixture_only'] != fixture:
        raise ValueError('fixture status must be explicit and consistent')
    if budgets is not None:
        profile = budgets['profiles'].get(runtime.get('budget_profile'))
        if profile is None or not set(semantic['categories']).issubset(profile['categories']):
            raise ValueError('category/profile mismatch')
    if prepared:
        if manifest.get('state') != 'prepared_engine_pending' or runtime.get('format') != 'collada':
            raise ValueError('prepared state/format is required')
        if runtime.get('approved') is not True:
            raise ValueError('standalone approval missing')
        if not _timestamp(source.get('downloaded_at')) or not SHA.fullmatch(str(source.get('archive_sha256', ''))):
            raise ValueError('source acquisition timestamp/hash missing')
        if type(source.get('archive_bytes')) is not int or source['archive_bytes'] <= 0:
            raise ValueError('source byte count missing')
        geometry = _mapping(manifest.get('geometry'), 'geometry')
        expected = {'units': 'meters', 'up_axis': '+Z', 'forward_axis': '+Y', 'handedness': 'right',
                    'pivot': 'ground_center', 'normalization_version': RECIPE}
        if any(geometry.get(k) != v or (type(v) is int and type(geometry.get(k)) is not int) for k, v in expected.items()):
            raise ValueError('geometry convention mismatch')
        dimensions = geometry.get('dimensions_m')
        if not isinstance(dimensions, list) or len(dimensions) != 3 or any(type(x) not in (int, float) or not math.isfinite(x) or x <= 0 for x in dimensions):
            raise ValueError('positive physical dimensions are required')
        quality = _mapping(manifest.get('quality'), 'quality')
        if any(quality.get(k) is not True for k in ('geometry_validated', 'textures_validated')) or quality.get('engine_validated') is not False:
            raise ValueError('standalone/engine validation state must be explicit')
        processing = _mapping(manifest.get('processing'), 'processing')
        if type(processing.get('recipe_version')) is not int or processing.get('recipe_version') != RECIPE or not isinstance(processing.get('tool_versions'), dict) or not processing['tool_versions'] or not isinstance(processing.get('changes'), list) or not processing['changes'] or not _timestamp(processing.get('prepared_at')):
            raise ValueError('processing provenance missing')
        if not SHA.fullmatch(str(processing.get('output_sha256', ''))):
            raise ValueError('output hash missing')
        lods = runtime.get('lods')
        safe_relative(runtime.get('artifact_root'))
        if not isinstance(lods, list) or len(lods) != 3 or any(not isinstance(x, dict) for x in lods):
            raise ValueError('three prepared LODs are required')
        for index, lod in enumerate(lods):
            _artifact(lod)
            if type(lod.get('triangles')) is not int or lod['triangles'] <= 0 or type(lod.get('vertices')) is not int or not 0 < lod['vertices'] <= 65000:
                raise ValueError('invalid LOD geometry counts')
            if budgets and lod['triangles'] > profile['lod_triangles'][index]:
                raise ValueError('LOD triangle budget exceeded')
            if index and lod['triangles'] > lods[index-1]['triangles']:
                raise ValueError('LOD complexity must not increase')
        if not isinstance(runtime.get('materials'), list) or not runtime['materials'] or any(not isinstance(x, dict) for x in runtime['materials']):
            raise ValueError('material metadata is required')
        if budgets and len(runtime['materials']) > profile['material_slots']:
            raise ValueError('material budget exceeded')
        for material in runtime['materials']:
            _artifact(material)
            if not IDENT.fullmatch(str(material.get('map_to', ''))):
                raise ValueError('material mapTo identity missing')
        textures = runtime.get('textures')
        if not isinstance(textures, list) or any(not isinstance(x, dict) for x in textures):
            raise ValueError('texture list is required')
        for texture in textures:
            _artifact(texture)
            if texture.get('format') != 'png' or any(type(texture.get(k)) is not int or texture[k] <= 0 for k in ('width', 'height', 'bytes')):
                raise ValueError('invalid texture budget metadata')
            if budgets and max(texture['width'], texture['height']) > profile['texture_max_dimension']:
                raise ValueError('texture dimension budget exceeded')
        if budgets and sum(t['bytes'] for t in textures) > profile['texture_bytes']:
            raise ValueError('texture byte budget exceeded')
        collision = _mapping(runtime.get('collision'), 'collision')
        if collision.get('mode') != 'none':
            _artifact(collision)
        if type(collision.get('triangles')) is not int or collision['triangles'] < 0 or (budgets and collision['triangles'] > profile['collision_triangles']):
            raise ValueError('collision budget exceeded')
    canonical(manifest)  # rejects NaN and non-JSON values
    return copy.deepcopy(manifest)


def _artifact(value):
    if not isinstance(value, dict) or not SHA.fullmatch(str(value.get('sha256', ''))):
        raise ValueError('artifact checksum missing')
    safe_relative(value.get('artifact'))


def safe_relative(value):
    if not isinstance(value, str) or not value or '\\' in value or ':' in value or '\x00' in value:
        raise ValueError('unsafe asset path')
    path = PurePosixPath(value)
    if any(ord(c) < 32 for c in value):
        raise ValueError('control character in asset path')
    if path.is_absolute() or any(p in ('', '.', '..') or p.endswith((' ', '.')) for p in value.split('/')):
        raise ValueError('unsafe asset path')
    if any(re.fullmatch(r'(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?', p) for p in path.parts):
        raise ValueError('reserved Windows asset path')
    return path


def safe_extract(archive, destination, limits):
    """Preflight all members, then stream CRC-checked bytes into a new directory."""
    archive, destination = Path(archive), Path(destination)
    if archive.stat().st_size > limits['archive_bytes_per_asset']:
        raise ValueError('archive byte budget exceeded')
    if destination.exists():
        raise ValueError('extraction destination must be new')
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        if len(members) > limits['archive_members_per_asset']:
            raise ValueError('archive member budget exceeded')
        expanded, names = 0, set()
        for info in members:
            name = info.filename.rstrip('/') if info.is_dir() else info.filename
            path = safe_relative(name)
            if name.casefold() in names:
                raise ValueError('duplicate archive paths')
            names.add(name.casefold())
            mode = info.external_attr >> 16
            if info.flag_bits & 1 or (stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR)):
                raise ValueError('encrypted or special archive member')
            if not info.is_dir() and path.suffix.lower() not in ALLOWED_SUFFIXES:
                raise ValueError('unsupported archive member type')
            expanded += info.file_size
            if expanded > limits['expanded_bytes_per_asset'] or info.file_size > max(1, info.compress_size) * limits['compression_ratio_limit']:
                raise ValueError('archive expansion budget exceeded')
        destination.mkdir(parents=True)
        for info in members:
            target = destination.joinpath(*PurePosixPath(info.filename).parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            count = 0
            with source.open(info) as incoming, target.open('xb') as output:
                while chunk := incoming.read(min(1 << 20, info.file_size - count + 1)):
                    count += len(chunk)
                    if count > info.file_size:
                        raise ValueError('archive member exceeded declared size')
                    output.write(chunk)
            if count != info.file_size:
                raise ValueError('truncated archive member')
    return {'members': len(members), 'expanded_bytes': expanded, 'archive_sha256': sha256(archive)}


def validate_gltf(path):
    """Reject network/escaping resources before an importer gets the untrusted file."""
    path = Path(path)
    if path.suffix.lower() != '.gltf' or path.stat().st_size > MAX_JSON:
        raise ValueError('a bounded local glTF JSON is required')
    data = _mapping(json.loads(path.read_text(encoding='utf-8')), 'glTF root')
    asset = _mapping(data.get('asset'), 'glTF asset')
    if asset.get('version') != '2.0' or data.get('skins') or data.get('animations'):
        raise ValueError('only static glTF 2.0 is supported')
    required = data.get('extensionsRequired', [])
    if not isinstance(required, list) or any(not isinstance(x, str) for x in required):
        raise ValueError('glTF required extensions must be a string array')
    if set(required) - {'KHR_materials_pbrSpecularGlossiness'}:
        raise ValueError('required glTF extensions need an explicit supported recipe')
    root = path.parent.resolve()
    resources = []
    for group in ('buffers', 'images'):
        entries = data.get(group, [])
        if not isinstance(entries, list) or any(not isinstance(x, dict) for x in entries):
            raise ValueError(f'glTF {group} must be an array of objects')
        for entry in entries:
            uri = entry.get('uri')
            if not isinstance(uri, str) or urlsplit(uri).scheme or urlsplit(uri).netloc or '?' in uri or '#' in uri:
                raise ValueError('glTF resources must be explicit local files')
            relative = safe_relative(unquote(uri))
            target = root.joinpath(*relative.parts)
            if not target.resolve().is_relative_to(root) or target.is_symlink() or not target.is_file():
                raise ValueError('missing or escaping glTF resource')
            if target.stat().st_size > 64 << 20:
                raise ValueError('glTF resource exceeds byte budget')
            resources.append(target)
    meshes, nodes = data.get('meshes'), data.get('nodes', [])
    if not isinstance(meshes, list) or not meshes or len(meshes) > 256 or not isinstance(nodes, list) or len(nodes) > 1024 or any(not isinstance(x, dict) for x in meshes + nodes):
        raise ValueError('glTF scene complexity exceeds preparation scope')
    for mesh in meshes:
        primitives = mesh.get('primitives', [])
        if not isinstance(primitives, list) or any(not isinstance(x, dict) for x in primitives):
            raise ValueError('glTF mesh primitives must be objects')
        for primitive in primitives:
            if not isinstance(primitive.get('attributes', {}), dict):
                raise ValueError('glTF primitive attributes must be an object')
            if primitive.get('mode', 4) != 4 or primitive.get('targets'):
                raise ValueError('only static triangle primitives are supported')
    materials = data.get('materials', [])
    if not isinstance(materials, list) or any(not isinstance(m, dict) for m in materials):
        raise ValueError('glTF materials must be an array of objects')
    for material in materials:
        pbr = material.get('pbrMetallicRoughness', {})
        extensions = material.get('extensions', {})
        if not isinstance(pbr, dict) or not isinstance(extensions, dict):
            raise ValueError('glTF material sections must be objects')
        spec_gloss = extensions.get('KHR_materials_pbrSpecularGlossiness')
        if spec_gloss is not None and not isinstance(spec_gloss, dict):
            raise ValueError('glTF specular-glossiness extension must be an object')
    accessors = data.get('accessors', [])
    if not isinstance(accessors, list) or len(accessors) > 4096 or any(not isinstance(a, dict) or type(a.get('count')) is not int or not 0 < a['count'] <= 500000 for a in accessors):
        raise ValueError('source accessor complexity exceeds preparation scope')
    return data, resources


def attribution(manifests):
    lines = ['# Local asset attribution', '']
    for manifest in sorted(manifests, key=lambda m: m['asset_id']):
        source, license = manifest['source'], manifest['license']
        lines += [f"## {source['model_name']}", '', license['attribution'],
                  f"Source: {source['model_url']}", f"Creator: {source['creator_name']} ({source['creator_url']})",
                  f"License: {license['label']} ({license['url']})",
                  'Changes: ' + '; '.join(manifest['processing']['changes']),
                  f"Original SHA256: {source['archive_sha256']}", '']
    return '\n'.join(lines)


def verify_outputs(root, manifest, budgets):
    """Check actual local artifacts before exposing a manifest as a catalog candidate."""
    import xml.etree.ElementTree as ET
    from PIL import Image
    root = Path(root).resolve()
    relative = safe_relative(manifest['runtime']['artifact_root'])
    directory = root.joinpath(*relative.parts)
    if not directory.resolve().is_relative_to(root):
        raise ValueError('artifact root escapes catalog')
    for part in (directory,) + tuple(directory.parents):
        if part == root:
            break
        if part.is_symlink():
            raise ValueError('artifact root contains a symlink')
    runtime = manifest['runtime']
    records = runtime['lods'] + runtime['textures'] + runtime['materials']
    if runtime['collision']['mode'] != 'none':
        records.append(runtime['collision'])
    files = {}
    for record in records:
        relative = safe_relative(record['artifact'])
        path = directory.joinpath(*relative.parts)
        if not path.resolve().is_relative_to(directory.resolve()) or not path.is_file() or path.is_symlink():
            raise ValueError('missing/escaping prepared artifact')
        for parent in path.parents:
            if parent == directory:
                break
            if parent.is_symlink():
                raise ValueError('prepared artifact contains a symlink')
        if type(record.get('bytes')) is not int or not 0 < record['bytes'] <= 64 << 20 or path.stat().st_size != record['bytes'] or sha256(path) != record['sha256']:
            raise ValueError('prepared artifact checksum/size mismatch')
        files[relative.as_posix()] = path
    combined = hashlib.sha256(canonical([(name, sha256(path)) for name, path in sorted(files.items())])).hexdigest()
    if combined != manifest['processing']['output_sha256']:
        raise ValueError('prepared output aggregate mismatch')
    ns = '{http://www.collada.org/2005/11/COLLADASchema}'
    for lod in runtime['lods'] + ([runtime['collision']] if runtime['collision']['mode'] != 'none' else []):
        path = files[lod['artifact']]
        document = ET.parse(path).getroot()
        if document.find('.//' + ns + 'up_axis').text != 'Z_UP' or document.find('.//' + ns + 'unit').get('meter') != '1':
            raise ValueError('prepared DAE convention mismatch')
        counts = 0
        for triangles in document.iter(ns + 'triangles'):
            count = int(triangles.get('count'))
            inputs = max(int(node.get('offset')) for node in triangles.findall(ns+'input')) + 1
            if len(triangles.find(ns+'p').text.split()) != count * 3 * inputs:
                raise ValueError('prepared DAE triangle stream mismatch')
            counts += count
        if counts != lod['triangles']:
            raise ValueError('actual prepared triangle count mismatch')
    for texture in runtime['textures']:
        with Image.open(files[texture['artifact']]) as picture:
            if picture.format != 'PNG' or picture.size != (texture['width'], texture['height']):
                raise ValueError('actual prepared texture dimensions mismatch')
            picture.verify()
    definitions = json.loads(files[runtime['materials'][0]['artifact']].read_text(encoding='utf-8'))
    for material in runtime['materials']:
        if definitions.get(material['map_to'], {}).get('mapTo') != material['map_to']:
            raise ValueError('prepared material mapTo mismatch')
    return True


def write_catalog(root, manifests, budgets):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    admitted, rejected = [], []
    for candidate in manifests:
        try:
            manifest = validate_manifest(candidate, prepared=True, budgets=budgets)
            verify_outputs(root, manifest, budgets)
            admitted.append(manifest)
        except (ValueError, OSError, KeyError):
            rejected.append(str(candidate.get('asset_id', 'unknown')))
    admitted.sort(key=lambda m: m['asset_id'])
    if len({m['asset_id'] for m in admitted}) != len(admitted):
        raise ValueError('duplicate catalog identity')
    content = {'schema_version': SCHEMA, 'assets': admitted, 'fallback': 'procedural'}
    content['revision'] = hashlib.sha256(canonical(content)).hexdigest()
    with compiledcache._guard(root / '.catalog.lock'):
        atomic_json(root / 'catalog.json', content)
        target = root / 'ATTRIBUTION.md'
        temporary = staging.tmp_for(str(target))
        Path(temporary).write_text(attribution(admitted), encoding='utf-8')
        staging.replace_cached(temporary, str(target))
    return dict(revision=content['revision'], admitted=len(admitted), rejected=rejected)


class Catalog:
    """Immutable local snapshot; missing/removed/ineligible assets select procedural fallback."""
    def __init__(self, root, budgets):
        path = Path(root) / 'catalog.json'
        self.assets, self.revision = (), None
        if path.is_file() and path.stat().st_size <= MAX_JSON:
            catalog = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(catalog, dict):
                raise ValueError('invalid catalog structure')
            revision = catalog.pop('revision', None)
            if revision != hashlib.sha256(canonical(catalog)).hexdigest():
                raise ValueError('catalog checksum mismatch')
            assets = []
            catalog_assets = catalog.get('assets')
            if (catalog.get('schema_version') != SCHEMA or type(catalog.get('schema_version')) is not int
                    or not isinstance(catalog_assets, list) or catalog.get('fallback') != 'procedural'):
                raise ValueError('invalid catalog structure')
            for manifest in catalog_assets:
                try:
                    validated = validate_manifest(manifest, prepared=True, budgets=budgets)
                    verify_outputs(root, validated, budgets)
                    assets.append(validated)
                except (ValueError, OSError, KeyError, TypeError, AttributeError):
                    # Artifacts can be pruned independently; stale entries simply lose eligibility.
                    continue
            self.assets = tuple(assets)
            self.revision = revision

    def candidates(self, category, region=None, climate=None):
        return tuple(copy.deepcopy(m) for m in self.assets if category in m['semantic']['categories']
                     and (not m['semantic']['regions'] or region in m['semantic']['regions'])
                     and (not m['semantic']['climate_types'] or climate in m['semantic']['climate_types']))
