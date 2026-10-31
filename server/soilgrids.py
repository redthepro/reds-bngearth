"""Prepared SoilGrids 0-5 cm mean composition; never a tyre-friction calibration.

Publisher units: sand/clay g/kg and cfvo cm3/dm3. Both normalize by 1000,
but cfvo is a *volume* fraction and must not be added to the mass fractions.
https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_01.html
Only local PreparedStore lookups occur here; acquisition/reprojection is offline.
"""
import numpy as np

from provider_contract import Evidence

PROVIDER_ID = "soilgrids"
LICENSE = "CC-BY-4.0"
DEPTH = "0-5cm"
STATISTIC = "mean"
UNITS = {"sand": "g/kg", "clay": "g/kg", "cfvo": "cm3/dm3"}
OUTPUTS = {"sand": "sand_fraction", "clay": "clay_fraction", "cfvo": "cfvo_fraction"}
OUTPUT_UNITS = {"sand_fraction": "mass_fraction", "clay_fraction": "mass_fraction",
                "cfvo_fraction": "volume_fraction"}
SOURCE_URL = "https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_01.html"


def _check_metadata(metadata, layer):
    if not metadata:
        return  # an unprepared subset is missing, not an all-zero composition
    required = {"units": UNITS[layer], "depth": DEPTH, "statistic": STATISTIC,
                "crs": "EPSG:4326"}
    for key, expected in required.items():
        if metadata.get(key) != expected:
            raise ValueError(f"SoilGrids {layer}: expected {key}={expected!r}")
    for key, expected in (("provider_id", PROVIDER_ID), ("layer_id", layer)):
        if key in metadata and metadata[key] != expected:
            raise ValueError(f"SoilGrids {layer}: incompatible {key}")


def _provenance(samples):
    result = []
    for layer, sample in samples:
        for item in sample.provenance:
            item = dict(item)
            item["normalization"] = {"output": OUTPUTS[layer], "scale": 0.001,
                                     "units": OUTPUT_UNITS[OUTPUTS[layer]]}
            if item not in result:
                result.append(item)
    return tuple(result)


def _quality(valid, samples):
    if any(sample.reason == 'invalid_or_corrupt' for _, sample in samples):
        return "transient_fallback"
    if not any(mask.any() for mask in valid.values()):
        return "missing"
    return "model_prediction" if all(mask.all() for mask in valid.values()) else "partial"


class SoilGrids:
    """Batched local composition evidence with an independent mask per property.

    Missing silt is not inferred, sand/clay are not renormalized, and coarse
    fragments are not mistaken for a mass fraction. Invalid raw values remain
    unknown even when a malformed source claims they are valid.
    """
    def __init__(self, store):
        self.store = store
        for layer in UNITS:
            _check_metadata(store.metadata(PROVIDER_ID, layer), layer)

    @property
    def revision(self):
        return self.store.revision

    def sample(self, lat, lon):
        values, valid, samples = {}, {}, []
        for layer, output in OUTPUTS.items():
            sample = self.store.sample(PROVIDER_ID, layer, lat, lon)
            for metadata in sample.provenance:
                _check_metadata(metadata, layer)
            raw = np.asarray(sample.values, np.float64)
            mask = np.asarray(sample.valid, bool) & np.isfinite(raw) & (raw >= 0) & (raw <= 1000)
            values[output] = np.where(mask, raw / 1000.0, np.nan)
            valid[output] = mask
            samples.append((layer, sample))
        return Evidence(values, valid, _provenance(samples), _quality(valid, samples))
