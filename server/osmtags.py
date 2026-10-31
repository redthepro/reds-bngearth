"""OpenStreetMap road tags (lane count, width, sidewalks, speed limit, lighting, smoothness...) for a map cell's
roads, joined to Overture's road segments by their OpenStreetMap way id (the user, 2026-10-22: "map data sources that
have more info about the roads, lane count, road types").

Overture's transportation theme has no lane count (removed in 2024, not back in schema v2.0.0) and few widths, but
98.6 % of its segments come from OpenStreetMap and keep the way id (sources[1].record_id "w<id>@<version>"). OSM US's
Layercake publishes every OSM highway as one GeoParquet file (https://layercake.openstreetmap.us/layers/highways/,
ODbL, ~29 GB, 2,467 row groups, spatially clustered, rebuilt about weekly), with the tags as columns and a bbox struct.
Like overture.py, only what a cell needs is read, through rangeread (netfetch's limits, the block cache), with DuckDB
reading the blocks: the footer once (9 MB), the bbox columns of the row groups whose statistics touch the cell, then
the tag columns of the row groups that hold a matching way (no geometry). The tag values are parsed in the query
(lanes "2;3" -> 2, width "7 m" / "23'" -> metres, maxspeed "30 mph" -> km/h).

The file is rewritten in place: a version is pinned by its ETag (the cache directory; every range request carries
If-Match, rangeread.Changed when it moved on), and the next version is taken on the next start (version()).

A layer of its own is not needed: mapdata joins the tags into the roads layer when it builds a cell (all processes
read the same cell file, so a road's lanes and width agree everywhere). Unreachable: the cell keeps no tags and the
roads use their width guesses (roads.carriageway), as before.
"""
import logging
import hashlib
from datetime import datetime, timedelta, timezone
import os
import re
import threading
import time

import numpy as np

import netfetch
import overture
import rangeread

log = logging.getLogger("earthd.mapdata")

URL = "https://data.openstreetmap.us/layercake/highways.parquet"
HOST = "data.openstreetmap.us"
VERSION_TTL_S = 7 * 86400       # a pinned version is checked again after this long (the file changes ~weekly)
RETRY_S = 3600                  # the source unreachable: asked again after this long (offline, blocked, gone)
PATH = "osm/highways.parquet"   # (the path DuckDB reads it under)

# The query: OSM way id and the parsed tags (NULL where absent or not understood).
_NUM = r"'^\s*([0-9]+(?:\.[0-9]+)?)'"
COLUMNS = {
    "lanes": f"TRY_CAST(regexp_extract(lanes, '^\\s*([0-9]+)', 1) AS INTEGER)",
    "width": (f"TRY_CAST(regexp_extract(width, {_NUM}, 1) AS DOUBLE) * "
              "CASE WHEN width LIKE '%ft%' OR width LIKE '%''%' THEN 0.3048 ELSE 1.0 END"),
    "maxspeed": (f"TRY_CAST(regexp_extract(maxspeed, {_NUM}, 1) AS DOUBLE) * "
                 "CASE WHEN maxspeed LIKE '%mph%' THEN 1.609344 ELSE 1.0 END"),
    "sidewalk": "sidewalk",
    "lit": "lit",
    "smoothness": "smoothness",
    "osm_surface": "surface",
    "osm_oneway": "oneway",
    "osm_layer": "TRY_CAST(layer AS INTEGER)",
}
USED = {"id", "bbox", "lanes", "width", "maxspeed", "sidewalk", "lit", "smoothness", "surface", "oneway", "layer"}
FLOATS = ("lanes", "width", "maxspeed", "osm_layer")
TEXTS = ("sidewalk", "lit", "smoothness", "osm_surface", "osm_oneway")

# Verified Layercake 36-column schema (pinned footer evidence, phase 04).
# Strings retain restrictions rather than converting an absent tag into permission.
SEMANTIC_COLUMNS = {name.replace(':', '_'): name for name in (
    'tracktype', 'access', 'motor_vehicle', 'bicycle', 'foot', 'crossing',
    'cycleway', 'cycleway:left', 'cycleway:right')}
COLUMNS.update({name: f'lower(trim("{source}"))' for name, source in SEMANTIC_COLUMNS.items()})
COLUMNS.update(osm_version='TRY_CAST(version AS INTEGER)', osm_timestamp='CAST(epoch_us(timestamp) AS VARCHAR)')
USED.update(SEMANTIC_COLUMNS.values())
USED.update(('version', 'timestamp'))
FLOATS += ('osm_version',)
TEXTS += tuple(SEMANTIC_COLUMNS) + ('osm_timestamp',)
COLUMN_SOURCES = dict(SEMANTIC_COLUMNS, osm_version='version', osm_timestamp='timestamp')


class TagCell(tuple):
    def __new__(cls, tags, ways, provenance):
        value = super().__new__(cls, (tags, ways))
        value.provenance = provenance
        return value


# Racetracks (GitHub #143): Overture's transportation theme has no highway=raceway (Daytona's oval and road course, the
# Nordschleife: nothing), so the cell's raceways come from the same Layercake file with their geometry: ways that are
# highway=raceway, read only from the row groups whose highway column has one (raceways()).
RACEWAY_COLUMNS = ("id", "highway", "name", "surface", "width", "lanes", "layer", "bridge", "tunnel", "oneway", "version")


def way_id(record):
    """The OSM way id of an Overture source record ("w1360453999@2" -> 1360453999), else None."""
    m = re.match(r"^w(\d+)", record or "")
    return int(m.group(1)) if m else None


class OsmTags:
    """Tags by OSM way id per map cell, from the pinned Layercake version."""

    def __init__(self, raw_dir, fetcher, url=URL):
        self.raw_dir, self.fetcher, self.url = raw_dir, fetcher, url
        self._lock = threading.Lock()
        self._remote = None         # (etag, RemoteFile)
        self._checked = 0.0
        self._reader = overture.Reader(raw_dir, fetcher)
        self._meta = None           # (RemoteFile, row groups) of the version in use: _groups

    def version(self, kind="demand"):
        """(etag, RemoteFile) of the version in use: the one pinned on disk while fresh, else the server's."""
        with self._lock:
            if self._remote is not None and time.time() - self._checked < VERSION_TTL_S:
                return self._remote
            pin = os.path.join(self.raw_dir, "osmtags", "pinned")
            etag = size = None
            try:
                with open(pin) as f:
                    etag, size, when = f.read().split("\n")[:3]
                    size, when = int(size), float(when)
                if time.time() - when > VERSION_TTL_S:
                    etag = None
            except (OSError, ValueError):
                etag = None
            if etag is None:
                down = os.path.join(self.raw_dir, "osmtags", "unreachable")
                try:
                    recent = time.time() - os.path.getmtime(down) < RETRY_S
                except OSError:
                    recent = False
                if recent:
                    raise rangeread.RangeError(f"{self.url}: unreachable at the last try")
                try:
                    r = self.fetcher.get(self.url, kind=kind, headers={"Range": "bytes=0-15"})
                    etag = r.headers.get("etag", "").strip()
                    m = rangeread.CONTENT_RANGE.match(r.headers.get("content-range", ""))
                    if not etag or not m or m.group(3) == "*":
                        raise rangeread.RangeError(f"{self.url}: no ETag or size")
                except Exception:
                    os.makedirs(os.path.dirname(down), exist_ok=True)
                    open(down, "w").close()
                    raise
                size = int(m.group(3))
                os.makedirs(os.path.dirname(pin), exist_ok=True)
                with open(pin + ".tmp", "w") as f:
                    f.write(f"{etag}\n{size}\n{time.time()}\n")
                os.replace(pin + ".tmp", pin)
            d = os.path.join(self.raw_dir, "osmtags", re.sub(r"[^A-Za-z0-9]+", "_", etag).strip("_"))
            self._remote = (etag, rangeread.RemoteFile([self.url], size, d, self.fetcher, if_match=etag))
            self._checked = time.time()
            return self._remote

    def _forget(self):
        with self._lock:
            self._remote = None
            try:
                os.remove(os.path.join(self.raw_dir, "osmtags", "pinned"))
            except OSError:
                pass

    def cell(self, bounds, kind="demand"):
        """({way id: {column: value}} of the OSM highways whose bbox touches bounds (lon0, lat0, lon1, lat1), [the
        highway=raceway ways among them: {"id", "coords" (k, 2) lon/lat, tags...}]): one pass for both. A version that
        changed under the reads is dropped and the cell asked once more on the new one."""
        for attempt in (0, 1):
            etag, remote = self.version(kind)
            try:
                tags, ways = self._cell(remote, bounds, kind)
                return TagCell(tags, ways, {
                    'id': 'osm-layercake-highways', 'source_revision': etag,
                    'release': etag, 'license': 'ODbL-1.0', 'licence': 'ODbL-1.0',
                    'url': self.url, 'crs': 'OGC:CRS84', 'units': 'tag-specific',
                    'attribution': '© OpenStreetMap contributors; OSM US Layercake',
                    'confidence': 'mapped_tags; missing is unknown',
                    'source_sha256': None, 'acquired_at': None,
                    'schema_footer_sha256': getattr(remote, '_layercake_footer_sha256', None),
                })
            except rangeread.Changed:
                if attempt:
                    raise
                log.info("osmtags: %s changed (was %s), taking the new version", self.url, etag)
                self._forget()
        return {}, []

    def _groups(self, con, uri, remote, kind):
        """{row group: {"first", "n", "bbox": {leaf: (min, max)}, "cols": {column: [byte ranges]}}} of the file,
        parsed once per version (0.6 s of every cell's two queries, 2026-10-22: Central Park's first block waited
        90 s for its cells)."""
        with self._lock:
            got = self._meta
        if got is not None and got[0] is remote:
            return got[1]
        tail = remote.read(max(0, remote.size - overture.FOOTER_GUESS), remote.size, kind)
        n = int.from_bytes(tail[-8:-4], "little")
        if n + 8 > len(tail):
            footer = remote.read(remote.size - n - 8, remote.size, kind)
        else:
            footer = tail[-n-8:]
        remote._layercake_footer_sha256 = hashlib.sha256(footer).hexdigest()
        meta = con.execute(f"""SELECT row_group_id, row_group_num_rows, path_in_schema, stats_min, stats_max,
            dictionary_page_offset, data_page_offset, total_compressed_size FROM parquet_metadata({uri})
            ORDER BY row_group_id, column_id""").fetchall()
        groups, first = {}, 0
        for rg, nrows, col, smin, smax, dict_off, data_off, size in meta:
            g = groups.get(rg)
            if g is None:
                g = groups[rg] = {"first": first, "n": nrows, "bbox": {}, "cols": {}}
                first += nrows
            offs = [o for o in (dict_off, data_off) if o]
            if not offs:
                continue
            top, leaf = col.split(", ")[0], col.split(", ")[-1]
            g["cols"].setdefault(top, []).append((min(offs), min(offs) + size))
            if top == "bbox":
                g["bbox"][leaf] = (smin, smax)
        with self._lock:
            self._meta = (remote, groups)
        return groups

    @staticmethod
    def _candidates(groups, bounds):
        lon0, lat0, lon1, lat1 = bounds

        def stat(g, leaf, i, default):
            try:
                return float(g["bbox"][leaf][i])
            except (KeyError, TypeError, ValueError):
                return default
        return [rg for rg, g in sorted(groups.items())
                if stat(g, "xmin", 0, -1e9) <= lon1 and stat(g, "xmax", 1, 1e9) >= lon0
                and stat(g, "ymin", 0, -1e9) <= lat1 and stat(g, "ymax", 1, 1e9) >= lat0]

    def _cell(self, remote, bounds, kind):
        lon0, lat0, lon1, lat1 = bounds
        rd = self._reader
        files = {PATH: remote}
        con, proto, errors = rd._connect(files, kind)
        try:
            uri = f"'{proto}://{PATH}'"
            groups = self._groups(con, uri, remote, kind)
            available = {column for group in groups.values() for column in group['cols']}
            cand = self._candidates(groups, bounds)
            if not cand:
                return {}, []
            want = {c for rg in cand for c in groups[rg]["cols"] if c in USED or c == "highway"}
            rd._fetch(files, {PATH: [r for rg in cand for c, rs in groups[rg]["cols"].items() if c in want
                                     for r in rs]}, kind)
            only = " OR ".join(f"file_row_number BETWEEN {groups[rg]['first']} AND "
                               f"{groups[rg]['first'] + groups[rg]['n'] - 1}" for rg in cand)
            box = (f"bbox.xmin <= {lon1!r} AND bbox.xmax >= {lon0!r} AND bbox.ymin <= {lat1!r} "
                   f"AND bbox.ymax >= {lat0!r}")
            cols = ", ".join(f"{expr if COLUMN_SOURCES.get(name, name) in available or name not in COLUMN_SOURCES else 'NULL'} AS \"{name}\""
                             for name, expr in COLUMNS.items())
            # only the ways with a tag the roads use (and the raceways): join() treats a way without one as one not
            # here, and building a dict per way of every highway in the cell was most of this thread's CPU
            # (osmtags, 14 CPU-s of a Deck's teleport, 2026-10-23)
            some = " OR ".join(f'"{c}" IS NOT NULL' for c in sorted((USED - {"id", "bbox", "version", "timestamp"}) & available)) or 'FALSE'
            rows = con.execute(f"""SELECT id, highway = 'raceway', file_row_number, {cols}
                FROM read_parquet({uri}, file_row_number = true)
                WHERE ({only}) AND {box} AND (highway = 'raceway' OR {some})""").fetchall()
            race = [r[2] for r in rows if r[1]]
            got = []
            if race:            # (rare: the raceways' own columns and geometry, of their row groups only)
                hit = sorted({rg for r in race for rg in cand
                              if groups[rg]["first"] <= r < groups[rg]["first"] + groups[rg]["n"]})
                rd._fetch(files, {PATH: [r for rg in hit for c in RACEWAY_COLUMNS + ("geometry",)
                                         for r in groups[rg]["cols"].get(c, [])]}, kind)
                try:            # (the geometry as its WKB bytes: no spatial extension needed)
                    con.execute("SET enable_geoparquet_conversion = false")
                except Exception:
                    pass
                rcols = ", ".join(f'"{c}"' if c in available else f'NULL AS "{c}"' for c in RACEWAY_COLUMNS)
                rows_in = ", ".join(str(int(r)) for r in race)
                got = con.execute(f"""SELECT {rcols}, geometry FROM read_parquet({uri}, file_row_number = true)
                    WHERE file_row_number IN ({rows_in})""").fetchall()
        except Exception:
            for e in errors:
                if isinstance(e, rangeread.Changed):
                    raise e
            raise
        finally:
            con.close()
        names = list(COLUMNS)
        tags = {int(r[0]): {k: v for k, v in zip(names, r[3:]) if v is not None} for r in rows}
        for values in tags.values():
            if 'osm_timestamp' in values:
                try:
                    instant = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(microseconds=int(values['osm_timestamp']))
                    values['osm_timestamp'] = instant.isoformat().replace('+00:00', 'Z')
                except (ValueError, OverflowError):
                    values.pop('osm_timestamp')
        ways = []
        for row in got:
            coords = _linestring(bytes(row[-1]))
            if coords is None or len(coords) < 2:
                continue
            d = {c: v for c, v in zip(RACEWAY_COLUMNS, row[:-1]) if v is not None}
            d["coords"] = coords
            ways.append(d)
        return tags, ways

RACEWAY_PAVED = {"", "asphalt", "concrete", "paved", "chipseal"}


def raceway_features(ways):
    """The raceways (raceways()) as road Features: class "raceway", ids "osm-raceway-<way id>" (after every Overture
    GERS id: mapgeom.concat appends them), OSM's surface / width / lanes / layer / bridge / tunnel as Overture's rules
    and the osm_* columns."""
    import mapgeom as mg
    ways = sorted(ways, key=lambda w: int(w["id"]))
    if not ways:
        return mg.empty("roads")
    coords, coord_off, flags, levels, surfaces, widths = [], [0], [], [], [], []
    for i, w in enumerate(ways):
        c = np.asarray(w["coords"], np.float64)
        coords.append(c)
        coord_off.append(coord_off[-1] + len(c))
        if str(w.get("bridge") or "no") not in ("no", ""):
            flags.append((i, "is_bridge", None))
        if str(w.get("tunnel") or "no") not in ("no", ""):
            flags.append((i, "is_tunnel", None))
        try:
            lv = float(w.get("layer"))
            if lv:
                levels.append((i, lv, None))
        except (TypeError, ValueError):
            pass
        sf = str(w.get("surface") or "").lower()
        surfaces.append((i, "paved" if sf in RACEWAY_PAVED else sf, None))
        try:
            wd = float(re.match(r"^\s*([0-9]+(?:\.[0-9]+)?)", str(w.get("width") or "")).group(1))
            widths.append((i, wd, None))
        except (AttributeError, ValueError):
            pass
    n = len(ways)
    lonlat = np.concatenate(coords)
    f = mg.Features("roads", mg.text_column([f"osm-raceway-{int(w['id']):012d}" for w in ways]),
                    np.full(n, mg.LINE, np.uint8), np.arange(n + 1, dtype=np.int64), np.arange(n + 1, dtype=np.int64),
                    np.array(coord_off, np.int64), lonlat)
    f.attrs = {"class": mg.text_column(["raceway"] * n), "subtype": mg.text_column(["road"] * n),
               "subclass": mg.text_column([""] * n), "name": mg.text_column([w.get("name") for w in ways]),
               "src_dataset": mg.text_column(["OpenStreetMap"] * n),
               "src_record": mg.text_column([f"w{int(w['id'])}@{int(w.get('version') or 0)}" for w in ways]),
               "cx": np.array([float(c[:, 0].min() + c[:, 0].max()) / 2 for c in coords]),
               "cy": np.array([float(c[:, 1].min() + c[:, 1].max()) / 2 for c in coords])}
    f.rules = {"flags": mg.rule_table(flags), "level": mg.rule_table(levels, "float"),
               "surface": mg.rule_table(surfaces), "width": mg.rule_table(widths, "float")}
    f.clipped = np.zeros(n, bool)
    return f


def _linestring(wkb):
    """(k, 2) lon/lat of a WKB LineString (2D, either byte order), else None."""
    import struct
    if len(wkb) < 9:
        return None
    end = "<" if wkb[0] == 1 else ">"
    typ = struct.unpack_from(end + "I", wkb, 1)[0]
    if typ % 1000 != 2 or typ >= 1000 or typ & 0x20000000:
        return None
    n = struct.unpack_from(end + "I", wkb, 5)[0]
    if 9 + 16 * n > len(wkb):
        return None
    return np.frombuffer(wkb, dtype=end + "f8", count=2 * n, offset=9).reshape(n, 2).astype(np.float64)


def join(feats, tags):
    """The tags of each road feature (its source record's way id) as attrs columns: floats (NaN where unknown) for
    FLOATS, text ('' where unknown) for TEXTS. feats: an Overture roads Features (attrs src_record)."""
    import mapgeom as mg
    recs = feats.attrs.get("src_record")
    n = len(feats)
    ids = [way_id(str(recs[i])) if recs is not None else None for i in range(n)]
    for c in FLOATS:
        feats.attrs["osm_" + c if not c.startswith("osm_") else c] = np.array(
            [float(tags.get(w, {}).get(c, np.nan)) if w is not None else np.nan for w in ids], np.float64)
    for c in TEXTS:
        feats.attrs[c if c.startswith("osm_") else "osm_" + c] = mg.text_column(
            [str(tags.get(w, {}).get(c) or "").strip() if w is not None else "" for w in ids])
    for c in SEMANTIC_COLUMNS:
        name = 'osm_' + c
        feats.attrs[name + '_known'] = np.asarray(feats.attrs[name] != '', bool)
    return feats


def motor_access(feats, i):
    """True/False/None for explicit unrestricted/forbidden/unknown motor access.

    Conditional/destination/agricultural restrictions remain tags, not guessed
    blanket prohibitions. A motor-specific tag takes precedence over access.
    """
    for name in ('osm_motor_vehicle', 'osm_access'):
        values = feats.attrs.get(name)
        value = str(values[i]).lower().strip() if values is not None else ''
        if not value:
            continue
        if value in ('no', 'private'):
            return False
        if value in ('yes', 'permissive', 'designated', 'official'):
            return True
        return None
    return None
