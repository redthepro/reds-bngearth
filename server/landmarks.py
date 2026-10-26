"""Bundled landmark models: bounded selection, background grounding, additive protocol 4 messages.

Meshes are already meter-scaled, Z-up and north-oriented by the offline converter. Their local
origin is the base center. The manifest never enables a model whose shape/material files are absent.
"""
import functools
from concurrent.futures import ThreadPoolExecutor
import json
import logging
import math
from pathlib import Path, PurePosixPath
import re
import sys
import time

import geodesy
import pacer

log = logging.getLogger(__name__)
ROOT = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parents[1])
MOD_ROOT = ROOT / "mod" / "beamng_earth"
VFS = "/levels/beamng_earth/art/shapes/beamngEarth/landmarks/"
MAX_MODELS = 32


def _number(value, lo, hi):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and lo <= value <= hi


def load_catalog(mod_root=MOD_ROOT):
    """Read once per process. Invalid or missing assets retain their generic building fallback."""
    mod_root = Path(mod_root).resolve()
    try:
        data = json.loads((mod_root / VFS.lstrip("/") / "manifest.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    rows = data.get("landmarks", []) if isinstance(data, dict) else []
    out, seen = [], set()
    for row in rows[:MAX_MODELS] if isinstance(rows, list) else []:
        if not isinstance(row, dict) or row.get("enabled") is False:
            continue
        ident = row.get("id")
        if not isinstance(ident, str) or not re.fullmatch(r"[a-z0-9_-]{1,64}", ident) or ident in seen:
            continue
        if not all(_number(row.get(k), lo, hi) for k, lo, hi in (("lat", -90, 90), ("lon", -180, 180), ("height_m", 0.01, 2000))):
            continue
        if not _number(row.get("range_m", 40000), 100, 100000) or not _number(row.get("ground_offset_m", 0), -100, 100):
            continue
        valid = True
        for field, ext in (("shape", ".cdae"), ("materials", ".materials.json")):
            path = row.get(field)
            prefix = VFS + ident + "/"
            if not isinstance(path, str) or not path.startswith(prefix) or not path.endswith(ext) or "\\" in path or ".." in PurePosixPath(path).parts:
                valid = False
                break
            local = (mod_root / path.lstrip("/")).resolve()
            if not local.is_relative_to(mod_root) or not local.is_file():
                valid = False
                break
        if not valid:
            continue
        ids = row.get("exclude_ids", [])
        if not isinstance(ids, list) or len(ids) > 128 or not all(isinstance(i, str) and len(i) <= 128 for i in ids):
            continue
        out.append({"id": ident, "title": str(row.get("title", ident))[:200], "lat": float(row["lat"]),
                    "lon": float(row["lon"]), "shape": row["shape"], "materials": row["materials"],
                    "height_m": float(row["height_m"]), "range_m": float(row.get("range_m", 40000)),
                    "ground_offset_m": float(row.get("ground_offset_m", 0)), "exclude_ids": tuple(ids)})
        seen.add(ident)
    return tuple(out)


@functools.lru_cache(maxsize=1)
def catalog():
    return load_catalog()


@functools.lru_cache(maxsize=1)
def _excluded():
    return frozenset(fid for row in catalog() for fid in row["exclude_ids"])


def excluded_feature(key):
    """Visual-only near/far building filter; callers retain procedural collision geometry."""
    return str(key) in _excluded()


def excluded_ids():
    """Canonical effective visual exclusions for peer identity; the same startup snapshot as rendering."""
    return tuple(sorted(_excluded()))


def select(rows, frame, focus, view=None):
    """Select by car OR camera, including distant silhouettes; no elevation/network work here."""
    eyes = [frame.enu_to_geodetic(*xy, 0.0)[:2] for xy in (focus, view) if xy is not None]
    return tuple(row for row in rows if any(geodesy.haversine_m(float(la), float(lo), row["lat"], row["lon"]) <= row["range_m"] for la, lo in eyes))


def grounded(server, anchor, row):
    """Worker thread only: airport/sea adjustments, then road grading as in terrain and props."""
    import numpy as np
    la, lo = np.array([row["lat"]]), np.array([row["lon"]])
    z = server.ground(anchor.frame, la, lo, server.dem.sample(la, lo, anchor.zooms[0]))
    grader = server.grader_for(anchor)
    if grader is not None:
        z = grader.apply(la, lo, z)
    height = float(np.asarray(z).reshape(-1)[0]) + row.get("ground_offset_m", 0)
    if not math.isfinite(height):
        raise ValueError("non-finite landmark ground")
    x, y, up = anchor.frame.geodetic_to_enu(row["lat"], row["lon"], height)
    return {k: row[k] for k in ("id", "title", "lat", "lon", "shape", "materials", "height_m")} | {
        "x": float(x), "y": float(y), "z": float(up)}


class Tracker:
    """One connection, one bounded worker job; stale anchor/range results are never emitted."""
    def __init__(self, rows=None):
        self.rows = catalog() if rows is None else rows
        self.busy = False
        self.wanted = None
        self.sent = None
        self.cache_aid = None
        self.cache = {}
        self.retry = (None, 0.0)
        self.pool = None
        self.closed = False

    def close(self):
        self.closed = True
        if self.pool is not None:
            self.pool.shutdown(wait=False, cancel_futures=True)
            self.pool = None

    def on_pose(self, conn, aid, focus, view=None):
        anchor = conn.s.anchors.get(aid)
        if self.closed or anchor is None or not self.rows:
            return
        chosen = select(self.rows, anchor.frame, focus, view)
        key = (aid, tuple(row["id"] for row in chosen))
        self.wanted = key
        if (self.busy or key == self.sent or conn.terrain_wait > 0 or conn.teleport_pending
                or (self.retry[0] == key and time.monotonic() < self.retry[1])):
            return
        self.busy = True
        if self.pool is None:
            self.pool = ThreadPoolExecutor(1, thread_name_prefix="landmarks")

        async def run():
            try:
                if self.closed:
                    return
                if self.cache_aid != aid:
                    self.cache_aid, self.cache = aid, {}
                def build():
                    result = []
                    for row in chosen:
                        if self.closed or self.wanted != key:
                            break
                        pacer.hold()  # a newly requested terrain block wins before the next model
                        if row["id"] not in self.cache:
                            self.cache[row["id"]] = grounded(conn.s, anchor, row)
                        result.append(self.cache[row["id"]])
                    return result
                rows = await conn.loop.run_in_executor(self.pool, build)
                if not self.closed and conn.active == aid and self.wanted == key and not conn.teleport_pending:
                    conn.send({"type": "landmarks", "anchorId": aid, "rows": rows})
                    self.sent = key
            except Exception as exc:
                log.debug("landmark grounding: %s", exc)
                self.retry = (key, time.monotonic() + 5.0)
            finally:
                self.busy = False
        conn.spawn(run())
