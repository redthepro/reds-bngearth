"""Prepared GLC_FCS10 functional cover, with explicit WorldCover fallback.

Lookup only reads the bounded local PreparedStore. Acquisition and reprojection
are offline operations; this module neither downloads maps nor infers species
or tree height. Canopy structure remains the existing Meta/WRI CHM's job.
"""
from dataclasses import dataclass

import numpy as np

import maprasters
from provider_contract import MAX_POINTS, Sample, prepare_raster

PROVIDER_ID = "glc_fcs10"
LAYER_ID = "classification"
VERSION = 1
SOURCE_REVISION = "GLC_FCS10-2023-v1-Zenodo-14729665"
SOURCE_URL = "https://zenodo.org/records/14729665"
LICENSE = "CC-BY-4.0"
ATTRIBUTION = "GLC_FCS10 2023, Zenodo record 14729665; CC BY 4.0"
LEGEND_SHA256 = "bdbfc22d96c6a5f8ebe6a16b94429f8e4338dd6babe46363c6e68bb932c9c896"

# Compact functional categories, not exact species or ground-material parameters.
FUNCTIONAL_TYPES = (
    "unknown", "herbaceous_cropland", "woody_cropland", "broadleaf_evergreen",
    "broadleaf_deciduous", "needleleaf_evergreen", "needleleaf_deciduous", "mixed_forest",
    "evergreen_shrub", "deciduous_shrub", "grassland", "lichen_moss", "sparse_vegetation",
    "swamp", "marsh", "flooded_flat", "saline", "mangrove", "salt_marsh", "tidal_flat",
    "built", "bare", "water", "snow_ice", "tree_unspecified", "shrub_unspecified",
    "cropland_unspecified", "wetland_unspecified",
)
(UNKNOWN, HERBACEOUS_CROPLAND, WOODY_CROPLAND, BROADLEAF_EVERGREEN, BROADLEAF_DECIDUOUS,
 NEEDLELEAF_EVERGREEN, NEEDLELEAF_DECIDUOUS, MIXED_FOREST, EVERGREEN_SHRUB, DECIDUOUS_SHRUB,
 GRASSLAND, LICHEN_MOSS, SPARSE_VEGETATION, SWAMP, MARSH, FLOODED_FLAT, SALINE, MANGROVE,
 SALT_MARSH, TIDAL_FLAT, BUILT, BARE, WATER, SNOW_ICE, TREE_UNSPECIFIED, SHRUB_UNSPECIFIED,
 CROPLAND_UNSPECIFIED, WETLAND_UNSPECIFIED) = range(len(FUNCTIONAL_TYPES))
SOURCE_UNKNOWN, SOURCE_GLC, SOURCE_WORLDCOVER = 0, 1, 2

# Verified from the publisher's GLC_FCS10maps_colormap.lyr (30 renderer classes).
# Keep native classes separately: open/closed canopy and urban/rural are not lost
# merely because two classes share a functional category.
CLASS_MAP = {
    11: HERBACEOUS_CROPLAND, 12: WOODY_CROPLAND, 20: HERBACEOUS_CROPLAND,
    51: BROADLEAF_EVERGREEN, 52: BROADLEAF_EVERGREEN,
    61: BROADLEAF_DECIDUOUS, 62: BROADLEAF_DECIDUOUS,
    71: NEEDLELEAF_EVERGREEN, 72: NEEDLELEAF_EVERGREEN,
    81: NEEDLELEAF_DECIDUOUS, 82: NEEDLELEAF_DECIDUOUS,
    91: MIXED_FOREST, 92: MIXED_FOREST, 121: EVERGREEN_SHRUB, 122: DECIDUOUS_SHRUB,
    130: GRASSLAND, 140: LICHEN_MOSS, 150: SPARSE_VEGETATION,
    181: SWAMP, 182: MARSH, 183: FLOODED_FLAT, 184: SALINE, 185: MANGROVE,
    186: SALT_MARSH, 187: TIDAL_FLAT, 191: BUILT, 192: BUILT, 200: BARE,
    210: WATER, 220: SNOW_ICE,
}
WORLDCOVER_MAP = {
    10: TREE_UNSPECIFIED, 20: SHRUB_UNSPECIFIED, 30: GRASSLAND,
    40: CROPLAND_UNSPECIFIED, 50: BUILT, 60: BARE, 70: SNOW_ICE,
    80: WATER, 90: WETLAND_UNSPECIFIED, 95: MANGROVE, 100: LICHEN_MOSS,
}
CONIFER_TYPES = (NEEDLELEAF_EVERGREEN, NEEDLELEAF_DECIDUOUS)
BROADLEAF_TYPES = (BROADLEAF_EVERGREEN, BROADLEAF_DECIDUOUS)
TREE_TYPES = BROADLEAF_TYPES + CONIFER_TYPES + (MIXED_FOREST, TREE_UNSPECIFIED)
# These hints may choose an existing stock visual group at a CHM top; they must
# never overwrite measured height, identify species, or turn mixed into pure forest.
GROUP_HINTS = {**{x: "conifer" for x in CONIFER_TYPES},
               **{x: "broadleaf" for x in BROADLEAF_TYPES}}


def _numeric(values):
    raw = np.asarray(values)
    if raw.dtype.kind not in "fiu":
        raise TypeError("categorical raster values must be numeric, not bool/object/string")
    return raw.astype(np.float64, copy=False)


def _decode(values, valid, mapping):
    raw = _numeric(values)
    if valid is None:
        mask = np.ones(raw.shape, bool)
    else:
        mask = np.asarray(valid)
        if mask.dtype != np.bool_:
            raise TypeError("categorical validity mask must be boolean")
        raw, mask = np.broadcast_arrays(raw, mask)
    eligible = mask & np.isfinite(raw) & (raw == np.floor(raw))
    result = np.zeros(raw.shape, np.uint8)
    for code, functional in mapping.items():
        result[eligible & (raw == code)] = functional
    return result, result != UNKNOWN


def decode(codes, valid=None):
    """GLC native classes -> functional uint8 and explicit known-category mask.

    Unrecognized values, including 0/255, are unknown rather than a real class.
    Validation precedes uint8 conversion, avoiding negative/large-value wrapping.
    """
    return _decode(codes, valid, CLASS_MAP)


def worldcover_types(codes, valid=None):
    """Coarse fallback, e.g. tree cover gives no leaf type or phenology."""
    return _decode(codes, valid, WORLDCOVER_MAP)


@dataclass
class Selection:
    functional: np.ndarray
    valid: np.ndarray
    source_index: np.ndarray
    native_class: np.ndarray
    provenance: tuple = ()
    reason: str = ""

    @property
    def quality(self):
        if not self.valid.any():
            return "missing"
        if not self.valid.all():
            return "partial"
        fine = np.any(self.source_index == SOURCE_GLC)
        coarse = np.any(self.source_index == SOURCE_WORLDCOVER)
        return "mixed" if fine and coarse else "semantic_prediction" if fine else "fallback"


def select(glc_codes, glc_valid, worldcover_codes, worldcover_valid):
    """Pointwise GLC priority, WorldCover fallback only where GLC is unknown."""
    glc, wc = np.broadcast_arrays(_numeric(glc_codes), _numeric(worldcover_codes))
    fine, fine_ok = decode(glc, glc_valid)
    coarse, coarse_ok = worldcover_types(wc, worldcover_valid)
    fine, fine_ok, coarse, coarse_ok = np.broadcast_arrays(fine, fine_ok, coarse, coarse_ok)
    valid = fine_ok | coarse_ok
    functional = np.where(fine_ok, fine, coarse).astype(np.uint8)
    source = np.where(fine_ok, SOURCE_GLC, np.where(coarse_ok, SOURCE_WORLDCOVER, SOURCE_UNKNOWN)).astype(np.uint8)
    native = np.where(fine_ok, glc, np.where(coarse_ok, wc, np.nan))
    return Selection(functional, valid, source, native)


def _worldcover_provenance(raster):
    return {
        "provider_id": "esa_worldcover", "source_revision": maprasters.WORLDCOVER_RELEASE,
        "license": "CC-BY-4.0", "source_crs": "EPSG:4326", "crs": "EPSG:4326",
        "units": "categorical_class", "nodata": 0, "resolution": {"nominal_m": 10},
        "confidence": "coarse_land_cover_no_leaf_or_species_inference",
        "acquired_at": None, "source_sha256": None,
        "files": list(getattr(raster, "files", ())),
        "attribution": "ESA WorldCover project 2021 / modified Copernicus Sentinel data (2021)",
    }


class Adapter:
    def __init__(self, prepared_store):
        self.store = prepared_store
        self.revision = f"glcfcs10-v{VERSION}:{prepared_store.revision}"

    @staticmethod
    def _validate_metadata(metadata):
        if not isinstance(metadata, dict):
            raise ValueError("GLC provenance must be a metadata object")
        for key, expected in (("units", "categorical_class"), ("crs", "EPSG:4326"),
                              ("provider_id", PROVIDER_ID), ("layer_id", LAYER_ID),
                              ("legend_sha256", LEGEND_SHA256)):
            if key in metadata and metadata[key] != expected:
                raise ValueError(f"GLC metadata has incompatible {key}")
        if "semantic_mapping_version" in metadata:
            version = metadata["semantic_mapping_version"]
            if type(version) is not int or version != VERSION:
                raise ValueError("GLC metadata has incompatible semantic_mapping_version")

    def lookup(self, lon, lat, worldcover=None):
        """Sample prepared classes and use an existing WorldCover Raster/Sample/array.

        A WorldCover Raster is the existing maprasters object, not a new fetcher.
        Missing or corrupt prepared data remains explicit and falls back pointwise.
        """
        lon, lat = np.broadcast_arrays(_numeric(lon), _numeric(lat))
        if lon.size > MAX_POINTS:
            raise ValueError("vegetation semantic lookup exceeds point budget")
        self._validate_metadata(self.store.metadata(PROVIDER_ID, LAYER_ID))
        domain = np.isfinite(lon) & np.isfinite(lat) & (lon >= -180) & (lon <= 180) & (lat >= -90) & (lat <= 90)
        fine = self.store.sample(PROVIDER_ID, LAYER_ID, lat, lon)
        for provenance in fine.provenance:
            self._validate_metadata(provenance)
        if isinstance(worldcover, Sample):
            coarse = worldcover
            coarse_provenance = worldcover.provenance
        else:
            if hasattr(worldcover, "sample"):
                values = np.full(lon.shape, np.nan)
                values[domain] = worldcover.sample(lon[domain], lat[domain])
            else:
                values = np.full(lon.shape, np.nan) if worldcover is None else worldcover
            values = np.broadcast_to(_numeric(values), lon.shape)
            coarse = Sample(values, np.isfinite(values))
            coarse_provenance = (_worldcover_provenance(worldcover),)
        # A malformed mask cannot expand a point query into an unbounded broadcast.
        fine_values, fine_mask = np.broadcast_to(_numeric(fine.values), lon.shape), np.broadcast_to(fine.valid, lon.shape)
        coarse_values = np.broadcast_to(_numeric(coarse.values), lon.shape)
        coarse_mask = np.broadcast_to(coarse.valid, lon.shape)
        selected = select(fine_values, fine_mask, coarse_values, coarse_mask)
        selected.valid &= domain
        selected.functional = np.where(selected.valid, selected.functional, UNKNOWN).astype(np.uint8)
        selected.source_index = np.where(selected.valid, selected.source_index, SOURCE_UNKNOWN).astype(np.uint8)
        selected.native_class = np.where(selected.valid, selected.native_class, np.nan)
        provenance = []
        if np.any(selected.source_index == SOURCE_GLC):
            provenance.extend(dict(p) for p in fine.provenance)
        if np.any(selected.source_index == SOURCE_WORLDCOVER):
            provenance.extend(dict(p) for p in coarse_provenance)
        selected.provenance = tuple(provenance)
        selected.reason = fine.reason
        if not selected.reason and np.any(fine_mask & domain & (selected.source_index != SOURCE_GLC)):
            selected.reason = "unknown_glc_class"
        return selected


def prepare(source_path, root, bounds, shape, metadata, *, band=1, src_crs=None, src_nodata=None):
    """Prepare one bounded local GeoTIFF window; do not fetch/extract an archive.

    Required acquisition provenance comes from the caller. The common preparer
    records the actual file SHA256/CRS and limits memory/output; nearest-neighbour
    reprojection preserves categorical values. Native class validity is checked
    at lookup, without inventing a missing-data code absent from source metadata.
    """
    meta = dict(metadata)
    meta.setdefault("source_revision", SOURCE_REVISION)
    meta.setdefault("license", LICENSE)
    meta.setdefault("units", "categorical_class")
    meta.setdefault("confidence", "remote_sensing_functional_class_not_species")
    meta.setdefault("nodata", None)
    meta.setdefault("resolution", {"nominal_m": 10})
    meta.setdefault("attribution", ATTRIBUTION)
    meta.setdefault("source_url", SOURCE_URL)
    meta.setdefault("legend_sha256", LEGEND_SHA256)
    meta.setdefault("semantic_mapping_version", VERSION)
    return prepare_raster(source_path, root, PROVIDER_ID, LAYER_ID, bounds, shape, meta,
                          band=band, src_crs=src_crs, src_nodata=src_nodata)
