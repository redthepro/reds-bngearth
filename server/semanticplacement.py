"""Deterministic, fail-closed mapping from normalized facts to visual assets.

This module compiles canonical placement records only. It does not create engine
objects, modify collision support, or infer a missing geographic fact.
"""
from collections import defaultdict
import hashlib
import json
import math
import re

import assetcatalog


SCHEMA_VERSION = 1
RULE_VERSION = 1
MAX_CANDIDATES = 12_000
MAX_OUTPUTS = 12_000
MAX_PER_CELL = 12_000
CATEGORIES = frozenset((
    "roadside.barrier",
    "vegetation.tree.broadleaf",
    "vegetation.tree.needleleaf",
    "terrain.rock",
))
CLEARANCES = ("road", "intersection", "building", "water")
UNKNOWN_WORDS = frozenset(("", "unknown", "unspecified", "none", "null", "n/a"))
SHA256 = re.compile(r"^[0-9a-f]{64}$")

# These are hard input/output ceilings, not measured engine frame-time budgets.
# Callers should pass tighter per-cell limits based on their existing renderer.
DEFAULT_LIMITS = {
    "max_candidates": MAX_CANDIDATES,
    "max_placements": MAX_OUTPUTS,
    "max_per_cell": MAX_PER_CELL,
    "min_clearances_m": {},
    "min_scale": 0.5,
    "max_scale": 2.0,
    "max_dimension_ratio": 1.25,
    "rock_slope_min": 0.02,
    "rock_slope_max": 1.0,
}


def canonical_bytes(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _text(value):
    return isinstance(value, str) and 0 < len(value.strip()) <= 512


def _known_text(value):
    return _text(value) and value.strip().lower() not in UNKNOWN_WORDS


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _positive_triplet(value):
    return (isinstance(value, (list, tuple)) and len(value) == 3
            and all(_finite(v) and v > 0 for v in value))


def _cell_key(value):
    if isinstance(value, str):
        return value if _known_text(value) else None
    if (isinstance(value, (list, tuple)) and len(value) == 3
            and all(type(part) is int for part in value)):
        return ":".join(str(part) for part in value)
    return None


def _clean_limits(limits):
    result = dict(DEFAULT_LIMITS)
    if limits is not None:
        if not isinstance(limits, dict):
            raise ValueError("placement limits must be an object")
        result.update(limits)
    for key in ("max_candidates", "max_placements", "max_per_cell"):
        if type(result.get(key)) is not int or not 0 < result[key] <= MAX_CANDIDATES:
            raise ValueError(f"invalid placement limit: {key}")
    for key in ("min_scale", "max_scale", "max_dimension_ratio", "rock_slope_min", "rock_slope_max"):
        if not _finite(result.get(key)) or result[key] <= 0:
            raise ValueError(f"invalid placement limit: {key}")
    if result["min_scale"] > result["max_scale"] or result["max_dimension_ratio"] < 1:
        raise ValueError("inconsistent placement scale limits")
    clearances = result.get("min_clearances_m")
    if not isinstance(clearances, dict):
        raise ValueError("minimum clearances must be an object")
    for category, thresholds in clearances.items():
        if category not in CATEGORIES or not isinstance(thresholds, dict):
            raise ValueError("invalid category clearance policy")
        if any(name not in CLEARANCES or not _finite(value) or value < 0
               for name, value in thresholds.items()):
            raise ValueError("invalid minimum clearance")
        if set(thresholds) != set(CLEARANCES):
            raise ValueError("every clearance dimension requires an explicit minimum")
    caps = result.get("resource_caps")
    if caps is not None:
        required = {"max_unique_assets", "max_unique_material_slots", "max_unique_texture_bytes",
                    "max_lod_triangles"}
        if not isinstance(caps, dict) or not required.issubset(caps):
            raise ValueError("resource caps must explicitly cover assets, materials, textures, and LOD triangles")
        for key in ("max_unique_assets", "max_unique_material_slots", "max_unique_texture_bytes"):
            if type(caps.get(key)) is not int or caps[key] < 0:
                raise ValueError(f"invalid resource cap: {key}")
        triangles = caps.get("max_lod_triangles")
        if (not isinstance(triangles, (list, tuple)) or len(triangles) != 3
                or any(type(value) is not int or value < 0 for value in triangles)):
            raise ValueError("max_lod_triangles must contain three nonnegative integers")
        result["resource_caps"] = {"max_unique_assets": caps["max_unique_assets"],
            "max_unique_material_slots": caps["max_unique_material_slots"],
            "max_unique_texture_bytes": caps["max_unique_texture_bytes"],
            "max_lod_triangles": list(triangles)}
    return result


def _asset_key(asset):
    source = asset.get("source")
    return source.get("model_uid", "") if isinstance(source, dict) else ""


def _asset_usable(asset, category, region, climate):
    """Return a reason when a catalog-like entry is not a supported visual asset."""
    if not isinstance(asset, dict):
        return "invalid_catalog_entry"
    if not _known_text(asset.get("asset_id")):
        return "asset_identity_missing"
    semantic = asset.get("semantic")
    runtime = asset.get("runtime")
    geometry = asset.get("geometry")
    quality = asset.get("quality")
    source = asset.get("source")
    if not all(isinstance(v, dict) for v in (semantic, runtime, geometry, quality, source)):
        return "incomplete_catalog_entry"
    if assetcatalog.license_status(asset.get("license")) != "eligible":
        return "asset_license_unverified"
    if asset.get("state") != "prepared_engine_pending" or runtime.get("approved") is not True:
        return "asset_not_approved"
    if runtime.get("fixture_only") is not False or source.get("provider") != "sketchfab":
        return "fixture_or_unknown_provider"
    if not re.fullmatch(r"[0-9a-f]{32}", str(source.get("model_uid", ""))):
        return "asset_source_identity_unverified"
    if quality.get("geometry_validated") is not True or quality.get("textures_validated") is not True:
        return "asset_quality_unverified"
    if category not in semantic.get("categories", []):
        return "category_mismatch"
    # An empty list means the uploader's applicability is unspecified, not global.
    regions, climates = semantic.get("regions"), semantic.get("climate_types")
    if not isinstance(regions, list) or not isinstance(climates, list):
        return "applicability_unverified"
    if region not in regions or climate not in climates:
        return "applicability_mismatch"
    dims = geometry.get("dimensions_m")
    if not _positive_triplet(dims):
        return "asset_dimensions_unverified"
    lods = runtime.get("lods")
    if not isinstance(lods, list) or len(lods) != 3 or any(not isinstance(row, dict) for row in lods):
        return "asset_lods_unverified"
    if any(not _known_text(row.get("artifact")) or type(row.get("triangles")) is not int
           or row["triangles"] <= 0 or not SHA256.fullmatch(str(row.get("sha256", "")))
           or type(row.get("bytes")) is not int or row["bytes"] <= 0 for row in lods):
        return "asset_lods_unverified"
    artifact_root = runtime.get("artifact_root")
    if not _known_text(artifact_root):
        return "asset_artifact_root_unverified"
    materials, textures = runtime.get("materials"), runtime.get("textures")
    if (not isinstance(materials, list) or not materials or any(not isinstance(row, dict) for row in materials)
            or not isinstance(textures, list) or any(not isinstance(row, dict) for row in textures)):
        return "asset_resources_unverified"
    if any(not _known_text(row.get("map_to")) or not _known_text(row.get("artifact"))
           or not SHA256.fullmatch(str(row.get("sha256", "")))
           or type(row.get("bytes")) is not int or row["bytes"] <= 0 for row in materials):
        return "asset_resources_unverified"
    if any(not _known_text(row.get("artifact")) or not SHA256.fullmatch(str(row.get("sha256", "")))
           or type(row.get("bytes")) is not int or row["bytes"] <= 0
           or row.get("format") != "png" for row in textures):
        return "asset_resources_unverified"
    return None


def _candidate_issue(candidate, limits):
    category = candidate.get("category")
    if category not in CATEGORIES:
        return "unsupported_category"
    position = candidate.get("position")
    if not isinstance(position, dict):
        return "position_missing"
    lon, lat, elevation = (position.get("lon"), position.get("lat"), position.get("elevation_m"))
    if not (_finite(lon) and -180 <= lon <= 180 and _finite(lat) and -90 <= lat <= 90):
        return "position_invalid"
    if not _finite(elevation):
        return "elevation_missing"
    if not _known_text(position.get("vertical_datum")):
        return "vertical_datum_unknown"
    if not _positive_triplet(candidate.get("target_dimensions_m")):
        return "target_dimensions_unknown"
    if not _finite(candidate.get("yaw_deg")):
        return "yaw_invalid"
    if candidate.get("yaw_reference") != "true_north_clockwise_degrees":
        return "yaw_reference_unknown"
    if not (_known_text(candidate.get("region")) and _known_text(candidate.get("climate"))):
        return "applicability_unknown"
    clearances = candidate.get("clearances_m")
    if not isinstance(clearances, dict):
        return "clearance_unknown"
    thresholds = limits["min_clearances_m"].get(category)
    if thresholds is None:
        return "clearance_policy_missing"
    for name in CLEARANCES:
        distance = clearances.get(name)
        if not _finite(distance) or distance < 0:
            return "clearance_unknown"
        if distance < thresholds[name]:
            return f"clearance_{name}"
    evidence = candidate.get("evidence")
    if not isinstance(evidence, dict):
        return "evidence_missing"
    if category == "roadside.barrier":
        cls = str(evidence.get("classification", "")).strip().lower()
        if (evidence.get("observation") != "source_observed"
                or cls not in ("barrier", "guardrail", "guard_rail", "roadside_barrier")
                or not _known_text(evidence.get("source_id"))):
            return "barrier_not_explicitly_observed"
    elif category in ("vegetation.tree.broadleaf", "vegetation.tree.needleleaf"):
        family = "broadleaf" if category.endswith("broadleaf") else "needleleaf"
        if (evidence.get("provider_id") != "glc_fcs10"
                or evidence.get("functional_class") != family
                or evidence.get("valid") is not True
                or not _known_text(evidence.get("source_revision"))):
            return "tree_family_unverified"
    elif category == "terrain.rock":
        cover = str(evidence.get("landcover_class", "")).strip().lower()
        slope = evidence.get("slope_fraction")
        if (evidence.get("landcover_valid") is not True or cover not in ("bare", "sparse_vegetation")
                or evidence.get("slope_valid") is not True or not _finite(slope)
                or slope < limits["rock_slope_min"] or slope > limits["rock_slope_max"]):
            return "rock_evidence_unverified"
    return None


def _matches_scale(asset, target_dimensions, limits):
    asset_dims = asset["geometry"]["dimensions_m"]
    ratios = [float(target) / float(source) for target, source in zip(target_dimensions, asset_dims)]
    if any(not math.isfinite(ratio) or ratio <= 0 for ratio in ratios):
        return None
    scale = sum(ratios) / len(ratios)
    if (scale < limits["min_scale"] or scale > limits["max_scale"]
            or max(ratios) / min(ratios) > limits["max_dimension_ratio"]):
        return None
    return scale


def _fallback(candidate, reason, detail):
    return {
        "candidate_id": candidate["candidate_id"],
        "cell_id": _cell_key(candidate["cell_id"]),
        "category": candidate.get("category"),
        "fallback": "procedural",
        "reason": reason,
        "placement_kind": "inferred_decoration",
        "explanation": detail,
    }


def resolve_candidates(candidates, catalog, *, world_seed, rule_version=RULE_VERSION, limits=None):
    """Resolve canonical candidate records to an approved asset or procedural fallback.

    Candidate dictionaries use the interface documented in the phase-06 handoff.
    The function never manufactures a candidate location: upstream data must supply
    its canonical position, stable ID, explicit owner, source evidence and clearances.
    """
    if not isinstance(candidates, (list, tuple)):
        raise ValueError("placement candidates must be a list")
    policy = _clean_limits(limits)
    if len(candidates) > policy["max_candidates"]:
        raise ValueError("placement candidate budget exceeded")
    if not _known_text(world_seed):
        raise ValueError("a documented stable world seed is required")
    if type(rule_version) is not int or rule_version <= 0:
        raise ValueError("rule version must be a positive integer")

    normalized = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("each placement candidate must be an object")
        candidate_id = candidate.get("candidate_id")
        cell_id = _cell_key(candidate.get("cell_id"))
        if not _known_text(candidate_id) or cell_id is None:
            raise ValueError("candidate identity and owner cell are required")
        if type(candidate.get("owned")) is not bool:
            raise ValueError("candidate ownership must be explicit")
        # Canonical serialization here rejects NaN/Infinity anywhere in evidence.
        canonical_bytes(candidate)
        normalized.append(candidate)
    normalized.sort(key=lambda row: (row["candidate_id"], _cell_key(row["cell_id"]), canonical_bytes(row)))
    input_digest = _digest(normalized)

    groups = defaultdict(list)
    for row in normalized:
        groups[row["candidate_id"]].append(row)
    duplicate_owner = {identity for identity, rows in groups.items()
                       if sum(row["owned"] is True for row in rows) > 1}

    revision = getattr(catalog, "revision", None) if catalog is not None else None
    if not _known_text(revision):
        catalog_state = "catalog_unavailable"
    elif not SHA256.fullmatch(revision):
        raise ValueError("catalog revision must be a SHA256")
    else:
        catalog_state = None

    owner_counts = {identity: sum(row["owned"] is True for row in rows)
                    for identity, rows in groups.items()}
    placements, fallbacks = [], []
    per_cell_count = defaultdict(int)
    resources_by_cell = defaultdict(lambda: {"assets": set(), "materials": set(), "textures": {},
                                             "lod_triangles": [0, 0, 0]})

    for candidate in normalized:
        identity = candidate["candidate_id"]
        cell = _cell_key(candidate["cell_id"])
        if identity in duplicate_owner:
            fallbacks.append(_fallback(candidate, "duplicate_owner",
                                       "Multiple cells claim ownership of this stable candidate ID."))
            continue
        if not candidate["owned"] or owner_counts[identity] == 0:
            fallbacks.append(_fallback(candidate, "not_owner",
                                       "This cell does not own the stable feature or placement candidate."))
            continue
        issue = _candidate_issue(candidate, policy)
        if issue:
            fallbacks.append(_fallback(candidate, issue,
                                       "Required source evidence, dimensions, applicability, datum, or clearance is missing or outside policy."))
            continue
        if catalog_state:
            fallbacks.append(_fallback(candidate, catalog_state,
                                       "No checksummed prepared catalog is available."))
            continue
        try:
            raw_assets = catalog.candidates(candidate["category"], candidate["region"], candidate["climate"])
        except (AttributeError, OSError, ValueError, TypeError):
            raw_assets = ()
        if not raw_assets:
            fallbacks.append(_fallback(candidate, "no_approved_asset",
                                       "The approved catalog has no asset for this category and applicability."))
            continue
        assets = []
        rejected_reasons = []
        for asset in raw_assets:
            reason = _asset_usable(asset, candidate["category"], candidate["region"], candidate["climate"])
            if reason:
                rejected_reasons.append(reason)
                continue
            scale = _matches_scale(asset, candidate["target_dimensions_m"], policy)
            if scale is None:
                rejected_reasons.append("dimension_mismatch")
                continue
            assets.append((asset, scale))
        assets.sort(key=lambda pair: (pair[0].get("asset_id", ""), _asset_key(pair[0])))
        if not assets:
            reason = "asset_dimensions_mismatch" if rejected_reasons and all(
                item == "dimension_mismatch" for item in rejected_reasons) else "no_applicable_asset"
            fallbacks.append(_fallback(candidate, reason,
                                       "No approved asset satisfies category, provenance, applicability, quality, and dimension constraints."))
            continue
        if len(placements) >= policy["max_placements"] or per_cell_count[cell] >= policy["max_per_cell"]:
            fallbacks.append(_fallback(candidate, "placement_budget_exceeded",
                                       "The deterministic placement output reached its configured budget."))
            continue

        choice_seed = {"world_seed": world_seed, "candidate_id": identity,
                       "catalog_revision": revision, "rule_version": rule_version}
        choice = int.from_bytes(hashlib.sha256(canonical_bytes(choice_seed)).digest()[:8], "big") % len(assets)
        asset, scale = assets[choice]
        caps = policy.get("resource_caps")
        if caps is None:
            fallbacks.append(_fallback(candidate, "resource_policy_missing",
                                       "No caller-supplied resource caps authorize this placement."))
            continue
        resources = resources_by_cell[cell]
        runtime = asset["runtime"]
        asset_identity = asset["asset_id"]
        root = runtime["artifact_root"]
        material_keys = {(row["map_to"], row["sha256"]) for row in runtime["materials"]}
        texture_keys = {row["sha256"]: row["bytes"] for row in runtime["textures"]}
        lod_counts = [row["triangles"] for row in runtime["lods"]]
        if any(digest in resources["textures"] and resources["textures"][digest] != size
               for digest, size in texture_keys.items()):
            fallbacks.append(_fallback(candidate, "resource_integrity_conflict",
                                       "One texture hash was reported with inconsistent byte sizes."))
            continue
        added_assets = resources["assets"] | {asset_identity}
        added_materials = resources["materials"] | material_keys
        added_texture_bytes = sum(resources["textures"].values()) + sum(
            size for digest, size in texture_keys.items() if digest not in resources["textures"])
        added_lod_triangles = [resources["lod_triangles"][i] + lod_counts[i] for i in range(3)]
        budget_reason = None
        if len(added_assets) > caps["max_unique_assets"]:
            budget_reason = "resource_budget_unique_assets"
        elif len(added_materials) > caps["max_unique_material_slots"]:
            budget_reason = "resource_budget_material_slots"
        elif added_texture_bytes > caps["max_unique_texture_bytes"]:
            budget_reason = "resource_budget_texture_bytes"
        else:
            for index, count in enumerate(added_lod_triangles):
                if count > caps["max_lod_triangles"][index]:
                    budget_reason = f"resource_budget_lod_triangles_{index}"
                    break
        if budget_reason:
            fallbacks.append(_fallback(candidate, budget_reason,
                                       "The placement would exceed a caller-supplied per-cell resource cap."))
            continue
        position = candidate["position"]
        evidence = candidate["evidence"]
        placements.append({
            "candidate_id": identity,
            "cell_id": cell,
            "category": candidate["category"],
            "asset_id": asset["asset_id"],
            "asset_uid": _asset_key(asset),
            "position": {"lon": float(position["lon"]), "lat": float(position["lat"]),
                         "elevation_m": float(position["elevation_m"]),
                         "vertical_datum": position["vertical_datum"]},
            "yaw_deg": float(candidate["yaw_deg"]) % 360.0,
            "yaw_reference": candidate["yaw_reference"],
            "scale": round(scale, 6),
            "source_evidence": evidence,
            "placement_kind": "inferred_decoration",
            "selection": {"rule_version": rule_version, "world_seed": world_seed,
                          "catalog_revision": revision},
            "resources": {
                "artifact_root": root,
                "lods": [{"artifact": lod["artifact"], "sha256": lod["sha256"],
                          "bytes": lod["bytes"], "triangles": lod["triangles"]}
                         for lod in runtime["lods"]],
                "materials": [{"map_to": material["map_to"], "artifact": material["artifact"],
                               "sha256": material["sha256"], "bytes": material["bytes"]}
                              for material in runtime["materials"]],
                "textures": [{"artifact": texture["artifact"], "sha256": texture["sha256"],
                              "bytes": texture["bytes"], "format": texture["format"]}
                             for texture in runtime["textures"]],
            },
            "collision": {"enabled": False, "mode": "none"},
            "fallback": "procedural",
            "explanation": "Selected deterministically from approved assets after evidence, clearance, applicability, and dimension checks.",
        })
        resources["assets"].add(asset_identity)
        resources["materials"].update(material_keys)
        resources["textures"].update(texture_keys)
        resources["lod_triangles"] = added_lod_triangles
        per_cell_count[cell] += 1

    resource_summary = []
    for cell, resources in sorted(resources_by_cell.items()):
        resource_summary.append({"cell_id": cell, "unique_assets": len(resources["assets"]),
            "unique_material_slots": len(resources["materials"]),
            "unique_texture_bytes": sum(resources["textures"].values()),
            "lod_triangles": list(resources["lod_triangles"])})

    result = {
        "schema_version": SCHEMA_VERSION,
        "rule_version": rule_version,
        "world_seed": world_seed,
        "catalog_revision": revision if catalog_state is None else None,
        "input_digest": input_digest,
        "placements": sorted(placements, key=lambda row: (row["candidate_id"], row["cell_id"])),
        "fallbacks": sorted(fallbacks, key=lambda row: (row["candidate_id"], row["cell_id"], row["reason"])),
        "fallback": "procedural",
        "resource_caps": policy.get("resource_caps"),
        "resource_summary": resource_summary,
        "limits": {"max_candidates": policy["max_candidates"], "max_placements": policy["max_placements"],
                   "max_per_cell": policy["max_per_cell"]},
    }
    result["plan_id"] = _digest(result)
    return result
