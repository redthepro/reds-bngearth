"""Prepared JRC Global Surface Water evidence; no shoreline or water-level API.

Occurrence is historical observation frequency, seasonality is the number of
months water was observed in the selected annual product. Their periods may
differ and are retained independently. Zero is valid dry evidence; 255 is nodata.
https://storage.googleapis.com/water-world/downloads_ancillary/DataUsersGuidev2024_v.5.pdf
"""
import numpy as np

from provider_contract import Evidence

PROVIDER_ID = "jrc_gsw"
LICENSE = "Copernicus terms"
ATTRIBUTION = "Source: EC JRC/Google; Pekel et al. (2016), High-resolution mapping of global surface water"
SOURCE_URL = "https://global-surface-water.appspot.com/download"
UNITS = {"occurrence": "percent", "seasonality": "months"}
OUTPUTS = {"occurrence": "occurrence_fraction", "seasonality": "seasonality_months"}
OUTPUT_UNITS = {"occurrence_fraction": "observation_fraction", "seasonality_months": "months"}
LIMITS = {"occurrence": 100, "seasonality": 12}


def _check_metadata(metadata, layer):
    if not metadata:
        return
    for key, expected in (("units", UNITS[layer]), ("crs", "EPSG:4326")):
        if metadata.get(key) != expected:
            raise ValueError(f"JRC {layer}: expected {key}={expected!r}")
    if not isinstance(metadata.get("period"), str) or not metadata["period"].strip():
        raise ValueError(f"JRC {layer}: an explicit product observation period is required")
    for key, expected in (("provider_id", PROVIDER_ID), ("layer_id", layer)):
        if key in metadata and metadata[key] != expected:
            raise ValueError(f"JRC {layer}: incompatible {key}")


class JRCWater:
    """Supplementary coarse observations, with no geometry or elevation mutation.

    The public API exposes measured occurrence/months instead of an arbitrary
    permanent-water threshold. Finer vectors retain shoreline authority.
    """
    def __init__(self, store):
        self.store = store
        for layer in UNITS:
            _check_metadata(store.metadata(PROVIDER_ID, layer), layer)

    @property
    def revision(self):
        return self.store.revision

    def sample(self, lat, lon):
        values, valid, provenance, transient = {}, {}, [], False
        for layer, output in OUTPUTS.items():
            sample = self.store.sample(PROVIDER_ID, layer, lat, lon)
            transient |= sample.reason == 'invalid_or_corrupt'
            for metadata in sample.provenance:
                _check_metadata(metadata, layer)
            raw = np.asarray(sample.values, np.float64)
            mask = np.asarray(sample.valid, bool) & np.isfinite(raw) & (raw >= 0) & (raw <= LIMITS[layer])
            # These integer products are prepared with nearest-neighbour sampling.
            mask &= raw == np.floor(raw)
            values[output] = np.where(mask, raw / 100.0 if layer == "occurrence" else raw, np.nan)
            valid[output] = mask
            for item in sample.provenance:
                item = dict(item)
                item["normalization"] = {"output": output, "scale": 0.01 if layer == "occurrence" else 1.0,
                                         "units": OUTPUT_UNITS[output]}
                if item not in provenance:
                    provenance.append(item)
        any_valid = any(mask.any() for mask in valid.values())
        quality = ("observational_evidence" if all(mask.all() for mask in valid.values()) else "partial") \
            if any_valid else "missing"
        if transient:
            quality = "transient_fallback"
        return Evidence(values, valid, tuple(provenance), quality)
