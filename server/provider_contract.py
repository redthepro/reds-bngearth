"""Bounded, immutable prepared semantic rasters. No network or GIS in lookup.

Acquisition uses the existing netfetch/rangeread stack. Rasterio is imported only
by the offline preparer. Catalog adoption is explicit: construct a new store.
"""
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import threading
import uuid
import zipfile

import numpy as np
import maprasters

SCHEMA = 1
MAX_INDEX = 1 << 20
MAX_META = 64 << 10
MAX_TILES = 128
MAX_PIXELS = 4_194_304
MAX_POINTS = 262_144
MAX_TILE_BYTES = 64 << 20
MAX_STORE_BYTES = 512 << 20
MAX_MEMORY_BYTES = 64 << 20
IDENTIFIER = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
REQUIRED = ('source_revision', 'license', 'acquired_at', 'source_crs', 'units',
            'nodata', 'resolution', 'confidence', 'source_sha256')


@dataclass
class Sample:
    values: np.ndarray
    valid: np.ndarray
    provenance: tuple = ()
    reason: str = ''


@dataclass
class Evidence:
    values: dict
    valid: dict
    provenance: tuple = ()
    quality: str = 'unknown'


def _json(value, cap=MAX_META):
    raw = json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
    if len(raw) > cap:
        raise ValueError('semantic metadata exceeds byte budget')
    return raw


def _ids(provider, layer):
    if not isinstance(provider, str) or not IDENTIFIER.fullmatch(provider) or not isinstance(layer, str) or not IDENTIFIER.fullmatch(layer):
        raise ValueError('invalid semantic provider/layer identifier')


def _meta(metadata, provider, layer):
    value = json.loads(_json(metadata))
    if not isinstance(value, dict) or any(k not in value for k in REQUIRED):
        raise ValueError('incomplete semantic provenance')
    for key in ('source_revision', 'license', 'acquired_at', 'source_crs', 'units'):
        if not isinstance(value[key], str) or not value[key].strip():
            raise ValueError(f'missing semantic {key}')
    if not isinstance(value['source_sha256'], str) or not re.fullmatch('[0-9a-f]{64}', value['source_sha256']):
        raise ValueError('source_sha256 must identify the actual prepared input')
    for key, expected in (('provider_id', provider), ('layer_id', layer), ('crs', 'EPSG:4326')):
        if key in value and value[key] != expected:
            raise ValueError(f'conflicting semantic {key}')
        value[key] = expected
    value.setdefault('vertical_datum', 'not_applicable')
    return value


def _geometry(raster):
    data = np.asarray(raster.data)
    if (raster.crs != 'EPSG:4326' or data.ndim != 2 or not data.size or data.size > MAX_PIXELS
            or data.dtype.kind not in 'fiu' or data.nbytes > MAX_TILE_BYTES):
        raise ValueError('unsupported or oversized prepared raster')
    x0, y0, dx, dy = map(float, (raster.x0, raster.y0, raster.dx, raster.dy))
    if not all(math.isfinite(x) for x in (x0, y0, dx, dy)) or dx <= 0 or dy <= 0:
        raise ValueError('invalid north-up raster transform')
    bounds = [x0, y0 - data.shape[0] * dy, x0 + data.shape[1] * dx, y0]
    if bounds[0] < -180 or bounds[2] > 180 or bounds[1] < -90 or bounds[3] > 90:
        raise ValueError('prepared raster outside geographic domain; split antimeridian tiles')
    return data, dict(x0=x0, y0=y0, dx=dx, dy=dy), bounds


def _index(root):
    path = root / 'index.json'
    if not path.exists():
        return {'schema': SCHEMA, 'tiles': []}
    if path.stat().st_size > MAX_INDEX:
        raise ValueError('semantic catalog exceeds byte budget')
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict) or type(value.get('schema')) is not int or value.get('schema') != SCHEMA or not isinstance(value.get('tiles'), list) or len(value['tiles']) > MAX_TILES:
        raise ValueError('invalid semantic catalog')
    for row in value['tiles']:
        if not isinstance(row, dict):
            raise ValueError('invalid semantic catalog entry')
        _ids(row['provider_id'], row['layer_id'])
        if not re.fullmatch('[0-9a-f]{64}\\.npz', row['path']) or row['path'] != row['sha256'] + '.npz':
            raise ValueError('unsafe semantic tile path')
        b = row['bounds']
        if len(b) != 4 or not all(type(x) in (int, float) and math.isfinite(x) for x in b) or not (-180 <= b[0] < b[2] <= 180 and -90 <= b[1] < b[3] <= 90):
            raise ValueError('invalid semantic coverage')
        _meta(row['metadata'], row['provider_id'], row['layer_id'])
        if type(row['bytes']) is not int or not 0 < row['bytes'] <= MAX_TILE_BYTES:
            raise ValueError('invalid semantic tile size')
    if sum(x['bytes'] for x in value['tiles']) > MAX_STORE_BYTES:
        raise ValueError('semantic store exceeds declared byte budget')
    return value


def write_tile(root, provider_id, layer_id, raster, valid, metadata):
    """Offline atomic publication, reusing the artifact store's OS transition lock."""
    from compiledcache import _guard
    _ids(provider_id, layer_id)
    metadata = _meta(metadata, provider_id, layer_id)
    data, transform, bounds = _geometry(raster)
    valid = np.asarray(valid)
    if valid.dtype != np.bool_ or valid.shape != data.shape or np.any(valid & ~np.isfinite(data)):
        raise ValueError('invalid explicit raster mask')
    if data.nbytes + valid.nbytes > MAX_TILE_BYTES:
        raise ValueError('semantic tile exceeds byte budget')
    header = dict(schema=SCHEMA, metadata=metadata, transform=transform, bounds=bounds)
    stream = io.BytesIO()
    np.savez_compressed(stream, data=data, valid=valid, meta=np.array(_json(header).decode()))
    payload = stream.getvalue()
    if len(payload) > MAX_TILE_BYTES or data.nbytes + valid.nbytes > MAX_TILE_BYTES:
        raise ValueError('semantic tile exceeds byte budget')
    digest = hashlib.sha256(payload).hexdigest()
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    row = dict(provider_id=provider_id, layer_id=layer_id, bounds=bounds,
               path=digest + '.npz', sha256=digest, bytes=len(payload), metadata=metadata)
    with _guard(root / '.catalog.lock', timeout=2.0):
        index = _index(root)
        # Old immutable payloads can still belong to a live snapshot. Keep them,
        # but refuse further preparation rather than grow disk use indefinitely.
        retained, count = 0, 0
        for existing in root.glob('*.npz'):
            retained += existing.stat().st_size
            count += 1
            if count > MAX_TILES * 2 or retained > MAX_STORE_BYTES:
                raise ValueError('retained semantic payload capacity reached')
        if not (root / row['path']).exists() and (count >= MAX_TILES * 2 or retained + len(payload) > MAX_STORE_BYTES):
            raise ValueError('retained semantic payload capacity reached')
        # An explicit preparation replaces only this exact provider/layer footprint.
        rows = [r for r in index['tiles'] if (r['provider_id'], r['layer_id'], r['bounds']) != (provider_id, layer_id, bounds)]
        rows.append(row)
        rows.sort(key=lambda r: (r['provider_id'], r['layer_id'], r['bounds'], r['sha256']))
        if len(rows) > MAX_TILES or sum(r['bytes'] for r in rows) > MAX_STORE_BYTES:
            raise ValueError('semantic catalog capacity reached')
        encoded = _json(dict(schema=SCHEMA, tiles=rows), MAX_INDEX)
        for path, content in ((root / row['path'], payload), (root / 'index.json', encoded)):
            tmp = root / (path.name + '.' + uuid.uuid4().hex + '.tmp')
            try:
                tmp.write_bytes(content)
                os.replace(tmp, path)
            finally:
                tmp.unlink(missing_ok=True)
    return row


class PreparedStore:
    def __init__(self, root):
        self.root = Path(root)
        self._index = _index(self.root)
        self.revision = hashlib.sha256(_json(self._index, MAX_INDEX)).hexdigest()
        self._cache, self._bytes = OrderedDict(), 0
        self._lock = threading.RLock()

    def metadata(self, provider_id, layer_id):
        rows = [r for r in self._index['tiles'] if r['provider_id'] == provider_id and r['layer_id'] == layer_id]
        return json.loads(_json(rows[0]['metadata'])) if rows else {}

    @property
    def index(self):
        return json.loads(_json(self._index, MAX_INDEX))

    def _load(self, row):
        with self._lock:
            key = row['sha256']
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            path = self.root / row['path']
            if path.stat().st_size != row['bytes']:
                raise ValueError('semantic tile length mismatch')
            payload = path.read_bytes()
            if hashlib.sha256(payload).hexdigest() != key:
                raise ValueError('semantic tile checksum mismatch')
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                members = archive.infolist()
                if len(members) != 3 or set(archive.namelist()) != {'data.npy', 'valid.npy', 'meta.npy'} or sum(x.file_size for x in members) > MAX_TILE_BYTES:
                    raise ValueError('invalid/oversized semantic arrays')
                # NPY headers can claim allocations larger than their ZIP body.
                # Check shape/dtype/length before np.load allocates anything.
                for member in members:
                    with archive.open(member) as fh:
                        version = np.lib.format.read_magic(fh)
                        if version == (1, 0):
                            shape, _, dtype = np.lib.format.read_array_header_1_0(fh)
                        elif version == (2, 0):
                            shape, _, dtype = np.lib.format.read_array_header_2_0(fh)
                        else:
                            raise ValueError('unsupported semantic NPY version')
                        count = math.prod(shape)
                        size = count * dtype.itemsize
                        if dtype.hasobject or size > MAX_TILE_BYTES or fh.tell() + size != member.file_size:
                            raise ValueError('invalid semantic NPY allocation')
                        if member.filename == 'meta.npy':
                            if shape != () or dtype.kind != 'U' or size > MAX_META * 4:
                                raise ValueError('invalid semantic metadata array')
                        elif len(shape) != 2 or count > MAX_PIXELS:
                            raise ValueError('invalid semantic raster dimensions')
            with np.load(io.BytesIO(payload), allow_pickle=False) as arrays:
                data, valid, header = arrays['data'], arrays['valid'], json.loads(str(arrays['meta']))
            if (not isinstance(header, dict) or type(header.get('schema')) is not int or
                    header != dict(schema=SCHEMA, metadata=row['metadata'], transform=header.get('transform'), bounds=row['bounds'])):
                raise ValueError('semantic tile descriptor mismatch')
            t = header['transform']
            raster = maprasters.Raster(data, 'EPSG:4326', t['x0'], t['y0'], t['dx'], t['dy'])
            _, _, bounds = _geometry(raster)
            if bounds != row['bounds'] or valid.dtype != np.bool_ or valid.shape != data.shape or np.any(valid & ~np.isfinite(data)):
                raise ValueError('semantic raster/mask mismatch')
            data.flags.writeable = valid.flags.writeable = False
            size = data.nbytes + valid.nbytes
            while self._cache and self._bytes + size > MAX_MEMORY_BYTES:
                _, (_, _, old) = self._cache.popitem(last=False)
                self._bytes -= old
            value = (raster, valid, size)
            self._cache[key] = value
            self._bytes += size
            return value

    def sample(self, provider_id, layer_id, lat, lon):
        _ids(provider_id, layer_id)
        lat, lon = np.broadcast_arrays(np.asarray(lat, float), np.asarray(lon, float))
        if lat.size > MAX_POINTS:
            raise ValueError('semantic lookup exceeds point budget')
        values, valid = np.full(lat.shape, np.nan), np.zeros(lat.shape, bool)
        provenance, failures = [], []
        domain = np.isfinite(lat) & np.isfinite(lon) & (lat >= -90) & (lat <= 90) & (lon >= -180) & (lon <= 180)
        for row in self._index['tiles']:
            if row['provider_id'] != provider_id or row['layer_id'] != layer_id:
                continue
            west, south, east, north = row['bounds']
            selected = domain & ~valid & (lon >= west) & (lon < east) & (lat > south) & (lat <= north)
            if not selected.any():
                continue
            try:
                raster, mask, _ = self._load(row)
            except (OSError, ValueError, KeyError, zipfile.BadZipFile) as exc:
                failures.append(type(exc).__name__)
                continue
            c = np.floor((lon[selected] - raster.x0) / raster.dx).astype(np.int64)
            r = np.floor((raster.y0 - lat[selected]) / raster.dy).astype(np.int64)
            inside = (r >= 0) & (r < mask.shape[0]) & (c >= 0) & (c < mask.shape[1])
            ok = np.zeros(r.shape, bool)
            ok[inside] = mask[r[inside], c[inside]]
            result = np.full(r.shape, np.nan)
            result[ok] = raster.data[r[ok], c[ok]]
            values[selected], valid[selected] = result, ok
            if ok.any():
                provenance.append(dict(json.loads(_json(row['metadata'])), prepared_sha256=row['sha256'], bounds=list(row['bounds'])))
        reason = 'invalid_or_corrupt' if failures else 'outside_prepared_coverage' if not valid.all() else ''
        return Sample(values, valid, tuple(provenance), reason)


def prepare_raster(source_path, root, provider_id, layer_id, bounds, shape, metadata, *, band=1, src_crs=None, src_nodata=None):
    """Offline nearest-neighbour reproject of a bounded local-file window.

    Source masks are warped separately, preserving valid zero and NaN/nodata.
    Native IGH is accepted as an actual WKT/PROJ CRS, never a fabricated EPSG ID.
    """
    import rasterio
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    from rasterio.vrt import WarpedVRT
    source_path = Path(source_path)
    if not source_path.is_file() or source_path.stat().st_size > (1 << 30):
        raise ValueError('preparation needs a bounded local raster file')
    with source_path.open('rb') as fh:
        if fh.read(4) not in (b'II*\x00', b'MM\x00*', b'II+\x00', b'MM\x00+'):
            raise ValueError('offline preparation accepts local GeoTIFF bytes only, not VRT/network references')
    height, width = shape
    if type(height) is not int or type(width) is not int or min(shape) <= 0 or height * width > MAX_PIXELS:
        raise ValueError('preparation output exceeds pixel budget')
    west, south, east, north = map(float, bounds)
    if not all(math.isfinite(x) for x in bounds) or not (-180 <= west < east <= 180 and -90 <= south < north <= 90):
        raise ValueError('invalid preparation bounds')
    digest = hashlib.sha256()
    with source_path.open('rb') as fh:
        for block in iter(lambda: fh.read(1 << 20), b''):
            digest.update(block)
    metadata = dict(metadata, source_sha256=digest.hexdigest())
    transform = from_bounds(west, south, east, north, width, height)
    with rasterio.Env(GDAL_CACHEMAX=64 << 20, GDAL_NUM_THREADS='1',
                      GDAL_DISABLE_READDIR_ON_OPEN='EMPTY_DIR', GDAL_PAM_ENABLED='NO'):
        with rasterio.open(source_path, driver='GTiff') as src:
            crs = src_crs or src.crs
            if not crs:
                raise ValueError('source raster has no CRS')
            metadata['source_crs'] = str(crs)
            nodata = src.nodata if src_nodata is None else src_nodata
            encode_nodata = lambda x: 'NaN' if x is not None and math.isnan(float(x)) else x
            metadata['native_nodata'] = encode_nodata(src.nodata)
            metadata['nodata'] = encode_nodata(nodata)
            metadata['source_pixel_size'] = [float(x) for x in src.res]
            metadata['source_bounds'] = list(src.bounds)
            metadata['source_transform'] = list(src.transform)[:6]
            metadata['prepared_pixel_size_degrees'] = [(east-west)/width, (north-south)/height]
            metadata['preparation'] = {'schema': SCHEMA, 'rasterio': rasterio.__version__,
                                       'gdal': rasterio.__gdal_version__, 'resampling': 'nearest',
                                       'warp_memory_mib': 64, 'threads': 1}
            source_transform = src.transform
            if (rasterio.crs.CRS.from_user_input(crs).to_epsg() == 4326 and west < 0
                    and src.bounds.left >= 0 and 180 < src.bounds.right <= 360.001):
                # Some publisher W180 tiles use 180..185 instead of -180..-175.
                # Shift only the longitude origin, never the samples or spacing.
                source_transform = rasterio.Affine(src.transform.a, src.transform.b, src.transform.c - 360,
                                                  src.transform.d, src.transform.e, src.transform.f)
                metadata['source_longitude_shift_degrees'] = -360
            metadata['effective_source_transform'] = list(source_transform)[:6]
            with WarpedVRT(src, src_crs=crs, src_nodata=nodata, crs='EPSG:4326',
                           src_transform=source_transform,
                           transform=transform, width=width, height=height, resampling=Resampling.nearest,
                           nodata=float('nan'), dtype='float32', warp_mem_limit=64) as vrt:
                masked = vrt.read(band, masked=True)
                data = np.asarray(masked.filled(np.nan), np.float32)
                valid = ~np.ma.getmaskarray(masked) & np.isfinite(data)
    raster = maprasters.Raster(data, 'EPSG:4326', west, north, (east-west)/width, (north-south)/height)
    return write_tile(root, provider_id, layer_id, raster, valid, metadata)
