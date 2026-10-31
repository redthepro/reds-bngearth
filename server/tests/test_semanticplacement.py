"""Fail-closed, deterministic semantic-to-asset resolution tests."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import semanticplacement as sp


REGION = "temperate_north"
CLIMATE = "temperate_moist"
REVISION = "a" * 64


class FakeCatalog:
    def __init__(self, assets=(), revision=REVISION):
        self.assets = tuple(assets)
        self.revision = revision

    def candidates(self, category, region=None, climate=None):
        return tuple(asset for asset in self.assets
                     if category in asset["semantic"]["categories"]
                     and region in asset["semantic"]["regions"]
                     and climate in asset["semantic"]["climate_types"])


def asset(asset_id="asset_a", category="vegetation.tree.broadleaf", dimensions=(2, 2, 10),
          region=REGION, climate=CLIMATE):
    return {
        "asset_id": asset_id,
        "state": "prepared_engine_pending",
        "source": {"provider": "sketchfab", "model_uid": (asset_id.encode().hex()[:32]).ljust(32, "0")},
        "semantic": {"categories": [category], "regions": [region], "climate_types": [climate]},
        "geometry": {"dimensions_m": list(dimensions)},
        "quality": {"geometry_validated": True, "textures_validated": True},
        "license": {"id": "cc-by", "url": "https://creativecommons.org/licenses/by/4.0/",
                    "redistribution_verified": True, "derivatives_verified": True,
                    "label": "Creative Commons Attribution 4.0",
                    "attribution": f"{asset_id} by a test creator, CC BY 4.0.",
                    "verified_at": "2026-10-26T00:00:00Z", "evidence_sha256": "f" * 64},
        "runtime": {"fixture_only": False, "approved": True, "artifact_root": f"assets/{asset_id}",
                    "lods": [{"artifact": f"lod{i}.dae", "sha256": f"{i:x}" * 64,
                              "bytes": 1000, "triangles": 100 // i} for i in range(1, 4)],
                    "materials": [{"map_to": "mat", "artifact": "main.materials.json",
                                   "sha256": "d" * 64, "bytes": 300}],
                    "textures": [{"artifact": "color.png", "sha256": "e" * 64,
                                  "bytes": 10, "width": 4, "height": 4, "format": "png"}]},
    }


def candidate(candidate_id="feature-1", category="vegetation.tree.broadleaf", **overrides):
    evidence = {
        "provider_id": "glc_fcs10", "functional_class": "broadleaf",
        "valid": True, "source_revision": "GLC_FCS10-2023-v1",
    }
    dimensions = [2, 2, 10]
    if category == "roadside.barrier":
        evidence = {"observation": "source_observed", "classification": "guard_rail",
                    "source_id": "overture-infrastructure:7"}
        dimensions = [4, 0.4, 0.8]
    elif category == "vegetation.tree.needleleaf":
        evidence["functional_class"] = "needleleaf"
    elif category == "terrain.rock":
        evidence = {"landcover_class": "bare", "landcover_valid": True,
                    "slope_fraction": 0.06, "slope_valid": True}
        dimensions = [2, 2, 2]
    row = {
        "candidate_id": candidate_id,
        "cell_id": [14, 4800, 6000],
        "owned": True,
        "category": category,
        "position": {"lon": -76.59, "lat": 39.28, "elevation_m": 20.0,
                     "vertical_datum": "NAVD88"},
        "target_dimensions_m": dimensions,
        "yaw_deg": 45.0,
        "yaw_reference": "true_north_clockwise_degrees",
        "evidence": evidence,
        "region": REGION,
        "climate": CLIMATE,
        "clearances_m": {"road": 8.0, "intersection": 25.0,
                          "building": 6.0, "water": 5.0},
    }
    row.update(overrides)
    return row


def limits(**overrides):
    result = {
        "max_candidates": 100,
        "max_placements": 100,
        "max_per_cell": 100,
        "resource_caps": {"max_unique_assets": 100, "max_unique_material_slots": 100,
                          "max_unique_texture_bytes": 10000, "max_lod_triangles": [10000, 10000, 10000]},
        "min_clearances_m": {category: {"road": 1.0, "intersection": 10.0,
                                        "building": 2.0, "water": 2.0}
                             for category in sp.CATEGORIES},
    }
    result.update(overrides)
    return result


class SemanticPlacementTest(unittest.TestCase):
    def resolve(self, rows, assets=None, **kwargs):
        options = {"world_seed": "fixed-world-seed", "limits": limits()}
        options.update(kwargs)
        return sp.resolve_candidates(rows, FakeCatalog(assets or []), **options)

    def test_three_vertical_slices_require_their_category_evidence(self):
        rows = [candidate("barrier-1", "roadside.barrier"),
                candidate("broadleaf-1"),
                candidate("needleleaf-1", "vegetation.tree.needleleaf"),
                candidate("rock-1", "terrain.rock")]
        assets = [asset("barrier", "roadside.barrier", (4, .4, .8)),
                  asset("broadleaf", "vegetation.tree.broadleaf"),
                  asset("needleleaf", "vegetation.tree.needleleaf"),
                  asset("rock", "terrain.rock", (2, 2, 2))]
        result = self.resolve(rows, assets)
        self.assertEqual({row["category"] for row in result["placements"]}, set(sp.CATEGORIES))
        self.assertEqual(result["fallbacks"], [])
        self.assertTrue(all(row["collision"] == {"enabled": False, "mode": "none"}
                            for row in result["placements"]))
        self.assertTrue(all(row["placement_kind"] == "inferred_decoration"
                            for row in result["placements"]))
        self.assertIn("materials", result["placements"][0]["resources"])
        self.assertIn("sha256", result["placements"][0]["resources"]["textures"][0])

    def test_barrier_requires_explicit_source_observation(self):
        row = candidate("barrier-1", "roadside.barrier")
        row["evidence"] = {"observation": "inferred", "classification": "guard_rail",
                           "source_id": "road-1"}
        result = self.resolve([row], [asset("barrier", "roadside.barrier", (4, .4, .8))])
        self.assertEqual(result["fallbacks"][0]["reason"], "barrier_not_explicitly_observed")

    def test_tree_requires_functional_landcover_not_regional_heuristic(self):
        row = candidate()
        row["evidence"] = {"provider_id": "heuristic", "functional_class": "broadleaf",
                           "valid": True, "source_revision": "local-hint"}
        result = self.resolve([row], [asset()])
        self.assertEqual(result["fallbacks"][0]["reason"], "tree_family_unverified")

    def test_rock_requires_valid_bare_ground_and_finite_qualifying_slope(self):
        row = candidate("rock-1", "terrain.rock")
        row["evidence"]["slope_fraction"] = float("nan")
        with self.assertRaises(ValueError):
            self.resolve([row], [asset("rock", "terrain.rock", (2, 2, 2))])
        row["evidence"]["slope_fraction"] = 0.0
        result = self.resolve([row], [asset("rock", "terrain.rock", (2, 2, 2))])
        self.assertEqual(result["fallbacks"][0]["reason"], "rock_evidence_unverified")

    def test_each_clearance_failure_falls_back(self):
        for name in sp.CLEARANCES:
            with self.subTest(clearance=name):
                row = candidate()
                row["clearances_m"][name] = 0.0
                result = self.resolve([row], [asset()])
                self.assertEqual(result["fallbacks"][0]["reason"], f"clearance_{name}")

    def test_unknown_datum_applicability_or_asset_scope_falls_back(self):
        row = candidate()
        row["position"]["vertical_datum"] = "unknown"
        result = self.resolve([row], [asset()])
        self.assertEqual(result["fallbacks"][0]["reason"], "vertical_datum_unknown")
        row = candidate()
        row["region"] = "unspecified"
        result = self.resolve([row], [asset()])
        self.assertEqual(result["fallbacks"][0]["reason"], "applicability_unknown")
        row = candidate()
        scoped = asset(region="", climate="")
        result = self.resolve([row], [scoped])
        self.assertEqual(result["fallbacks"][0]["reason"], "no_approved_asset")

    def test_empty_or_unapproved_catalog_uses_procedural_fallback(self):
        row = candidate()
        result = self.resolve([row], [])
        self.assertEqual(result["fallbacks"][0]["reason"], "no_approved_asset")
        missing = sp.resolve_candidates([row], None, world_seed="fixed-world-seed", limits=limits())
        self.assertEqual(missing["fallbacks"][0]["reason"], "catalog_unavailable")
        unapproved = asset()
        unapproved["runtime"]["approved"] = False
        result = self.resolve([row], [unapproved])
        self.assertEqual(result["fallbacks"][0]["reason"], "no_applicable_asset")

    def test_missing_or_ineligible_license_cannot_be_selected(self):
        row = candidate()
        for license_data in (None, {"id": "cc-by-nc", "url": "https://creativecommons.org/licenses/by-nc/4.0/"}):
            with self.subTest(license=license_data):
                value = asset()
                value["license"] = license_data
                result = self.resolve([row], [value])
                self.assertEqual(result["placements"], [])
                self.assertEqual(result["fallbacks"][0]["reason"], "no_applicable_asset")

    def test_yaw_requires_and_preserves_canonical_reference(self):
        valid = self.resolve([candidate()], [asset()])
        self.assertEqual(valid["placements"][0]["yaw_reference"], "true_north_clockwise_degrees")
        for yaw_reference in (None, "anchor_enu_radians", "magnetic_north_degrees"):
            row = candidate()
            if yaw_reference is None:
                row.pop("yaw_reference")
            else:
                row["yaw_reference"] = yaw_reference
            result = self.resolve([row], [asset()])
            self.assertEqual(result["placements"], [])
            self.assertEqual(result["fallbacks"][0]["reason"], "yaw_reference_unknown")

    def test_dimension_fit_requires_uniform_scale_in_range(self):
        row = candidate()
        row["target_dimensions_m"] = [4, 4, 20]
        result = self.resolve([row], [asset()])
        self.assertEqual(result["placements"][0]["scale"], 2.0)
        row["target_dimensions_m"] = [8, 2, 40]
        result = self.resolve([row], [asset()])
        self.assertEqual(result["fallbacks"][0]["reason"], "asset_dimensions_mismatch")
        row["target_dimensions_m"] = [8, 8, 40]
        result = self.resolve([row], [asset()])
        self.assertEqual(result["fallbacks"][0]["reason"], "asset_dimensions_mismatch")

    def test_non_owner_never_places_and_one_owned_duplicate_wins(self):
        owner = candidate("shared")
        other = candidate("shared", cell_id=[14, 4801, 6000], owned=False)
        result = self.resolve([other, owner], [asset()])
        self.assertEqual(len(result["placements"]), 1)
        self.assertEqual(result["placements"][0]["cell_id"], "14:4800:6000")
        self.assertEqual(result["fallbacks"][0]["reason"], "not_owner")

    def test_multiple_owned_duplicates_fail_closed(self):
        a = candidate("shared", cell_id=[14, 4800, 6000], owned=True)
        b = candidate("shared", cell_id=[14, 4801, 6000], owned=True)
        result = self.resolve([b, a], [asset()])
        self.assertEqual(result["placements"], [])
        self.assertEqual([row["reason"] for row in result["fallbacks"]], ["duplicate_owner", "duplicate_owner"])

    def test_replay_is_input_order_independent_and_plan_changes_with_revisions(self):
        rows = [candidate("tree-2"), candidate("tree-1")]
        catalog = FakeCatalog([asset("tree-a"), asset("tree-b")])
        a = sp.resolve_candidates(rows, catalog, world_seed="seed-a", limits=limits())
        b = sp.resolve_candidates(list(reversed(rows)), catalog, world_seed="seed-a", limits=limits())
        self.assertEqual(a, b)
        c = sp.resolve_candidates(rows, catalog, world_seed="seed-b", limits=limits())
        self.assertNotEqual(a["plan_id"], c["plan_id"])
        new_catalog = FakeCatalog(catalog.assets, revision="b" * 64)
        d = sp.resolve_candidates(rows, new_catalog, world_seed="seed-a", limits=limits())
        self.assertNotEqual(a["plan_id"], d["plan_id"])
        e = sp.resolve_candidates(rows, catalog, world_seed="seed-a", rule_version=2, limits=limits())
        self.assertNotEqual(a["plan_id"], e["plan_id"])

    def test_candidate_identity_changes_input_digest(self):
        row = candidate()
        first = self.resolve([row], [asset()])
        changed = candidate()
        changed["position"]["lon"] += 0.01
        second = self.resolve([changed], [asset()])
        self.assertNotEqual(first["input_digest"], second["input_digest"])

    def test_cell_and_total_output_limits_are_bounded(self):
        rows = [candidate("a"), candidate("b", cell_id=[14, 4801, 6000])]
        tight = limits(max_per_cell=1, max_placements=1)
        result = sp.resolve_candidates(rows, FakeCatalog([asset()]), world_seed="seed", limits=tight)
        self.assertEqual(len(result["placements"]), 1)
        self.assertEqual(result["fallbacks"][0]["reason"], "placement_budget_exceeded")
        with self.assertRaisesRegex(ValueError, "candidate budget"):
            sp.resolve_candidates(rows, FakeCatalog([asset()]), world_seed="seed",
                                  limits=limits(max_candidates=1))

    def test_resource_summary_deduplicates_reused_assets_materials_and_textures(self):
        rows = [candidate("a"), candidate("b")]
        result = self.resolve(rows, [asset()])
        summary = result["resource_summary"]
        self.assertEqual(summary, [{"cell_id": "14:4800:6000", "unique_assets": 1,
                                    "unique_material_slots": 1, "unique_texture_bytes": 10,
                                    "lod_triangles": [200, 100, 66]}])
        reversed_result = self.resolve(list(reversed(rows)), [asset()])
        self.assertEqual(result["resource_summary"], reversed_result["resource_summary"])

    def test_resource_caps_are_required_and_each_cap_falls_back(self):
        row = candidate()
        asset_one = asset("asset_one")
        no_caps = limits()
        no_caps.pop("resource_caps")
        result = sp.resolve_candidates([row], FakeCatalog([asset_one]), world_seed="seed", limits=no_caps)
        self.assertEqual(result["placements"], [])
        self.assertEqual(result["fallbacks"][0]["reason"], "resource_policy_missing")

        cases = [
            ("unique_assets", {"max_unique_assets": 0}, "resource_budget_unique_assets"),
            ("material_slots", {"max_unique_material_slots": 0}, "resource_budget_material_slots"),
            ("texture_bytes", {"max_unique_texture_bytes": 9}, "resource_budget_texture_bytes"),
            ("lod_triangles", {"max_lod_triangles": [99, 100, 66]}, "resource_budget_lod_triangles_0"),
        ]
        for label, update, reason in cases:
            with self.subTest(cap=label):
                cap = dict(limits()["resource_caps"])
                cap.update(update)
                result = sp.resolve_candidates([row], FakeCatalog([asset_one]), world_seed="seed",
                                               limits=limits(resource_caps=cap))
                self.assertEqual(result["placements"], [])
                self.assertEqual(result["fallbacks"][0]["reason"], reason)

    def test_resource_summary_is_separate_per_cell(self):
        rows = [candidate("a", cell_id=[14, 4800, 6000]),
                candidate("b", cell_id=[14, 4801, 6000])]
        result = self.resolve(rows, [asset()])
        self.assertEqual([row["unique_assets"] for row in result["resource_summary"]], [1, 1])
        self.assertEqual([row["unique_texture_bytes"] for row in result["resource_summary"]], [10, 10])

    def test_material_slots_deduplicate_across_assets_by_mapping_and_hash(self):
        rows = [candidate("barrier", "roadside.barrier"), candidate("tree")]
        assets = [asset("barrier_asset", "roadside.barrier", (4, .4, .8)), asset()]
        cap = limits()["resource_caps"]
        cap["max_unique_assets"] = 2
        cap["max_unique_material_slots"] = 1
        result = sp.resolve_candidates(rows, FakeCatalog(assets), world_seed="seed",
                                       limits=limits(resource_caps=cap))
        self.assertEqual(len(result["placements"]), 2)
        self.assertEqual(result["resource_summary"][0]["unique_assets"], 2)
        self.assertEqual(result["resource_summary"][0]["unique_material_slots"], 1)
        self.assertEqual(result["resource_summary"][0]["unique_texture_bytes"], 10)

    def test_malformed_owner_or_seed_is_rejected(self):
        row = candidate()
        row["owned"] = 1
        with self.assertRaisesRegex(ValueError, "ownership"):
            self.resolve([row], [asset()])
        with self.assertRaisesRegex(ValueError, "world seed"):
            sp.resolve_candidates([candidate()], FakeCatalog([asset()]), world_seed="unknown", limits=limits())


if __name__ == "__main__":
    unittest.main()
