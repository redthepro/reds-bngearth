"""World-plan identity for the first, materialized terrain compilation slice.

Canonical geographic facts are separate from anchor-specific prepared geometry.
An UNKNOWN dependency never becomes a FINAL road plan. Existing Grader profiles
remain per-run: compilation may reuse bytes only AFTER current preparation.
"""
from functools import lru_cache
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import threading

import numpy as np

SCHEMA_VERSION = 1
PLAN_VERSION = 1
MAX_FACT_CELLS = 64
MAX_FACT_BYTES = 2 << 20


def generation_enabled():
    return os.environ.get("BNGE_COMPILED_TERRAIN") == "1" and os.environ.get("BNGE_SHARED_TERRAIN", "on") != "off"


def runtime_receipt(dem, mapdata=None, settings=None):
    """Local generation receipt; never a claim of immutable provider coverage.

    A provider may expose a nonblocking worldplan_revision value/callable for an
    intentional snapshot upgrade. Unknown provider metadata stays unknown.
    """
    def revision(provider):
        value = getattr(provider, "worldplan_revision", None)
        return value() if callable(value) else value
    return {"dem_epoch": int(getattr(dem, "invalidation_epoch", 0)),
            "dem_revision": revision(dem), "mapdata_revision": revision(mapdata),
            "mapdata_release": getattr(mapdata, "release", None),
            "settings": settings or {}}


def geometry_settings(extra=None):
    """One settings receipt for both callers, including mutable rule constants."""
    import grading
    import heightmap
    import roads
    import surface
    def encode(value):
        if isinstance(value, (list, tuple)):
            return [encode(item) for item in value]
        if isinstance(value, (set, frozenset)):
            return sorted((encode(item) for item in value), key=canonical_bytes)
        if isinstance(value, float) and not np.isfinite(value):
            return {"special_float": repr(value)}
        return value
    result = {}
    for module in (grading, heightmap, roads, surface):
        result[module.__name__] = {name: encode(value) for name, value in vars(module).items()
            if name.isupper() and isinstance(value, (bool, int, float, str, list, tuple, set, frozenset))}
    result["caller"] = extra or {}
    return result


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def content_key(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def array_identity(value, *, data=False, allow_missing=False):
    if value is None:
        return {"missing": True}
    array = np.ascontiguousarray(value)
    if array.dtype.hasobject:
        raise ValueError("world plans cannot contain object arrays")
    if not allow_missing and array.dtype.kind in "fc" and not np.isfinite(array).all():
        raise ValueError("non-finite prepared input")
    identity = {"dtype": array.dtype.str, "shape": list(array.shape),
                "sha256": hashlib.sha256(array.tobytes(order="C")).hexdigest()}
    if data:
        def encode(item):
            if isinstance(item, list):
                return [encode(v) for v in item]
            if isinstance(item, float) and not np.isfinite(item):
                return {"missing": "nan" if np.isnan(item) else "+inf" if item > 0 else "-inf"}
            if isinstance(item, bytes):
                return {"bytes_hex": item.hex()}
            return item
        identity["data"] = encode(array.tolist())
    return identity


@lru_cache(maxsize=1)
def compiler_identity():
    """Process code is immutable; fresh processes fingerprint their actual files."""
    root = Path(__file__).resolve().parent
    names = ("worldplan.py", "terrainprep.py", "compiledcache.py", "heightmap.py", "surface.py",
             "grading.py", "roads.py", "roadmesh.py", "navgraph.py", "dem.py", "geodesy.py",
             "mapdata.py", "mapgeom.py", "airportgrade.py", "seacarve.py")
    files = {name: hashlib.sha256((root / name).read_bytes()).hexdigest()
             for name in names if (root / name).is_file()}
    dependencies = {}
    for name in ("numpy", "Pillow"):
        try:
            dependencies[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            dependencies[name] = None
    try:
        import earthfast
        native = Path(earthfast.__file__)
        files[native.name] = hashlib.sha256(native.read_bytes()).hexdigest()
        dependencies["earthfast"] = getattr(earthfast, "VERSION", None)
    except ImportError:
        dependencies["earthfast"] = None
    return {"files": files, "dependencies": dependencies}


def capture_context(grader=None, mapdata=None, dependency_keys=()):
    """Describe known consumed cells, without pretending the halo is closed.

    Re-reading a CellData does not certify freshness of MapData's own memory
    cache. This manifest is evidence, not a source invalidation mechanism.
    """
    import mapcells

    md = mapdata if mapdata is not None else getattr(grader, "md", None)
    # The caller supplies request-local dependencies. Never traverse the
    # anchor's ever-growing planning history to invent a dependency halo.
    keys = set(dependency_keys)
    if len(keys) > MAX_FACT_CELLS:
        return {"facts": [], "dependencies": [], "completeness": "unknown",
                "dependency_halo": {"cells": [], "closed": False},
                "errors": ["request fact-cell budget exceeded; snapshot omitted, not certified"]}
    facts, errors = [], []
    remaining = MAX_FACT_BYTES
    for key in sorted(keys, key=str):
        try:
            ck = mapcells.parse(key) if isinstance(key, str) else mapcells.CellKey(*key)
            cell = md.cell(ck, layers=("roads",), kind="demand") if md is not None else None
            if cell is None:
                errors.append(f"{ck}: no snapshot")
                continue
            feature = cell.layers.get("roads")
            arrays = {} if feature is None else feature.to_arrays("")
            # en is derived in a cell frame; retain canonical lon/lat and source
            # topology/attributes/rules/ownership, not a caller's XY cache.
            arrays.pop("en", None)
            used = sum(np.asarray(value).nbytes for value in arrays.values())
            if used > remaining:
                errors.append(f"{ck}: request fact-byte budget exceeded; snapshot omitted")
                break
            remaining -= used
            facts.append({"cell": list(ck), "versions": getattr(cell, "versions", {}),
                          "provenance": getattr(cell, "provenance", []),
                          "source_digest": cell.digest() if hasattr(cell, "digest") else None,
                          "roads_missing": feature is None,
                          "roads": {name: array_identity(value, data=True, allow_missing=True)
                                    for name, value in sorted(arrays.items())}})
        except Exception as exc:
            errors.append(f"{key}: {type(exc).__name__}")
    return {"facts": facts, "dependencies": [],
            "dependency_halo": {"cells": [row["cell"] for row in facts], "closed": False,
                "note": "observed grading cells only; arbitrary profile/tunnel dependencies are not certified"},
            "completeness": "unknown", "errors": errors,
            "support": {"terrain": "prepared", "bridge_navigation": "unknown",
                        "note": "no compatible bridge collision/deck proof supplied"}}


def build_descriptor(frame, cx, cy, size, q, arrays, context=None, settings=None):
    """Build separate stable fact and exact prepared-input identities.

    arrays must include every encoder input, including pre-hole masks and
    post-hole TER indices. Output names/request IDs are deliberately absent.
    """
    context = context or {"facts": [], "dependencies": [], "completeness": "unknown",
                          "dependency_halo": {"cells": [], "closed": False}}
    # A JSON round-trip detaches mutable caller dictionaries before publication.
    context = json.loads(canonical_bytes(context))
    canonical = {"schema": SCHEMA_VERSION, "version": PLAN_VERSION,
                 "coordinate_reference": "WGS84 longitude/latitude degrees",
                 "ownership": "mapcells.owner bbox-center; clipped polygons belong to clipping cell",
                 "vertical_reference": "provider-native metres; datum unknown unless dependency supplies evidence; existing geodesy interpretation preserved",
                 "facts": sorted(context.get("facts", []), key=canonical_bytes),
                 "dependencies": sorted(context.get("dependencies", []), key=canonical_bytes),
                 "dependency_halo": context.get("dependency_halo", {"closed": False}),
                 "completeness": context.get("completeness", "unknown"),
                 "support": context.get("support", {"bridge_navigation": "unknown"}),
                 "errors": context.get("errors", [])}
    dependencies = canonical["dependencies"]
    final = (canonical["completeness"] == "complete" and
             canonical["dependency_halo"].get("closed") is True and bool(dependencies) and
             not canonical["errors"] and
             all(d.get("complete") is True and bool(d.get("snapshot")) for d in dependencies))
    # This slice never persists grading decisions, even if an adapter certifies
    # its input. Materialized compiled bytes are a distinct, validated product.
    canonical_id = content_key(canonical)
    prepared = {"frame": {"kind": "WGS84 anchor ENU", "lat": frame.lat0,
                          "lon": frame.lon0, "h0": frame.h0},
                "grid": {"cx": float(cx), "cy": float(cy), "size": int(size),
                         "square_m": 2.0, "q_m": float(q), "lod": "near-terrain"},
                "inputs": {name: array_identity(value) for name, value in sorted(arrays.items())},
                "settings": settings or {}, "compiler": compiler_identity()}
    descriptor = {"schema_version": SCHEMA_VERSION, "canonical": canonical,
                  "canonical_id": canonical_id, "prepared": prepared,
                  "input_quality": "complete" if final else "provisional",
                  "decision_plan_persisted": False,
                  "reuse_policy": "compile-only; rerun current preparation before every lookup"}
    descriptor["prepared_id"] = content_key({"canonical_id": canonical_id, "prepared": prepared})
    return descriptor


class GenerationStore:
    """Atomically replace request snapshots; old holders keep old objects.

    Fact/DEM receipt changes must produce a new token. This is deliberately not
    a cache of persistent decisions; the store holds at most `limit` anchors.
    """
    def __init__(self, limit=2):
        self.limit = limit
        self._lock = threading.Lock()
        self._entries = {}
        self._serial = 0

    def acquire(self, anchor_key, receipt, factory):
        token = content_key(receipt)
        with self._lock:
            current = self._entries.get(anchor_key)
            if current is None or current[0] != token:
                self._serial += 1
                current = (token, factory(self._serial))
                self._entries[anchor_key] = current
            else:
                self._entries.pop(anchor_key)
                self._entries[anchor_key] = current
            while len(self._entries) > self.limit:
                self._entries.pop(next(iter(self._entries)))
            return current[1]
