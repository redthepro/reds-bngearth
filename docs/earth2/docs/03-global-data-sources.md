# Free worldwide data plan

Verified against publisher documentation on 2026-10-26. Availability and terms can change; pin the actual release and license when ingesting it. Do not download the whole planet during bootstrap.

## Keep and generalize what already exists

The reviewed code already uses Overture, OSM Layercake, ESA WorldCover, canopy-height data, terrain providers with global fallbacks, regional imagery with a global fallback, bathymetry, and weather. These are not new integrations.

Keep regional data as optional higher-quality providers. A region must still work when all regional providers are disabled. Apply the same adapter contract to Maryland imagery, local lidar, LANDFIRE, and future national datasets.

## Implementation order

| Priority | Dataset | Adds | Access and constraints |
|---|---|---|---|
| 1 | OSM US Layercake highways | Richer surface, access, path, and roadside semantics | Existing integration; direct global GeoParquet, ODbL |
| 2 | GLC_FCS10 | Finer global vegetation and land-cover types | 2023, 10 m, CC BY 4.0; staged archive extraction |
| 3 | SoilGrids | Sand/clay/coarse-fragment context for ground materials | Global 250 m, CC BY 4.0; raster downloads/WCS |
| 4 | JRC Global Surface Water | Water persistence and seasonality | Global 30 m; downloadable raster tiles |
| 5 | HydroRIVERS | River topology and ordering | Global vector downloads; HydroSHEDS terms |
| Optional | GlobalBuildingAtlas | Building height/LoD1 where useful | Large downloads; mixed and restrictive licenses |
| Visual | Sketchfab | Curated reusable models | Per-model license; account authentication |

### OSM Layercake: cheapest useful first improvement

The existing adapter reads several tags already. Expand it using fields actually present in the published schema, such as `tracktype`, `access`, `motor_vehicle`, `crossing`, and cycleway fields. Use them to improve material selection and plausible roadside details. OSM US is the publisher; the highways layer has worldwide scope.

Use selective GeoParquet reads and cached normalized results. Missing tags remain unknown. Do not infer a traffic restriction solely from appearance. Fields such as `turn:lanes` require a different/richer source if absent from this schema. Dataset reuse follows ODbL and OSM attribution requirements. [Publisher schema and download](https://layercake.openstreetmap.us/layers/highways/).

### GLC_FCS10: global vegetation distinctions

The 2023 10 m map offers 30 classes, including distinctions useful for broadleaf, needleleaf, evergreen, and deciduous vegetation. Combine it with existing canopy-height information and seeded variation. It is not a tree-species inventory.

The release is CC BY 4.0. Download archives by region, extract and prepare internal tiles before use. The published data archives total roughly 128 GB; this is not a lightweight per-player API. Keep ESA WorldCover as fallback and confidence-check fine classes. [Dataset](https://zenodo.org/records/14729665), [paper](https://essd.copernicus.org/articles/17/4039/2025/).

### SoilGrids: ground-material context

Use a small subset, initially the 0–5 cm mean sand, clay, and coarse-fragment layers, adding silt only when needed. At 250 m these are broad soil predictions, not tire friction measurements or road-surface measurements. Calibrate gameplay material choices independently and let explicit road surfaces take precedence.

ISRIC documents CC BY 4.0 and raster access through WebDAV/WCS. Its REST service is paused; avoid depending on it. Handle the native Interrupted Goode Homolosine projection explicitly during offline preparation. Avoid downloading every depth and uncertainty layer. [Documentation](https://docs.isric.org/globaldata/soilgrids/), [access FAQ](https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_02.html), [raster directory](https://files.isric.org/soilgrids/latest/data/).

### JRC Global Surface Water: persistence, not water depth

Use occurrence/seasonality to distinguish persistent water from intermittently wet terrain and to validate coarse water masks. The download page lists the 1984–2024 v1.5 product and 30 m tiles. It is not live flood depth or a precise roadside shoreline.

Use published downloadable rasters, pin the product version, and preserve the requested EC JRC/Google acknowledgment. The publisher describes unrestricted use under Copernicus terms. Check known alignment/observation issues before combining with finer geometry. [Downloads, terms, and known issues](https://global-surface-water.appspot.com/download).

### HydroRIVERS: optional topology

HydroRIVERS supplies global river reaches with network and hydrologic attributes, derived from 15-arcsecond data. It can help coherent rivers and downstream ordering. It is too coarse to dictate small roadside streams or exact banks.

Use released regional/global vector packages, then normalize once. Scientific, educational, and commercial use is allowed subject to HydroSHEDS terms and acknowledgment. Read the included license at ingestion. [Publisher](https://www.hydrosheds.org/products/hydrorivers).

### GlobalBuildingAtlas: separate optional experiment

Do not make this a default dependency. The project separates ODbL-derived polygons from CC BY-NC 4.0 data, including LoD1 and height products. Its code has separate terms. A free mod is not automatically a sufficient license determination.

Use published downloadable files, not automated streaming or bulk extraction from the demonstration WFS. Audit geometry CRS metadata and height quality. Keep asset/data lineage separate from Overture. [Publisher and license notice](https://github.com/zhu-xlab/GlobalBuildingAtlas#license-notice), [LoD1 downloads](https://huggingface.co/datasets/zhu-xlab/GBA.LoD1), [ODbL polygons](https://huggingface.co/datasets/zhu-xlab/GBA.ODbLPolygon).

## Coverage and fallback contract

Each provider declares geographic bounds, data validity, release ID, acquisition date, license, CRS/datum, units, no-data semantics, resolution, and confidence. Each result records the actual provider used.

Selection is property-specific. A finer canopy raster does not replace land-cover classification, a soil grid does not override a tagged paved road, and a river centerline is not a shoreline polygon.

Return an explicit missing/unsupported result, then use a documented fallback. Avoid silent all-zero tiles. Cache valid missing-data results with appropriate expiry; do not cache transient network failure as permanent absence.

## Not required for the first implementation

No paid imagery subscription, Google photogrammetry scraping, mandatory cloud hosting, full-planet downloads, Earth Engine dependency, live disaster feeds, or runtime Sketchfab searches. These do not directly address the first performance and global-quality goals.

## Phase 04 implementation

Prepared subsets are opt-in through `BNGE_SEMANTIC_ROOT`. The existing MapData
cache exposes optional `vegetation_types`, `soil` and `water_evidence` facts;
default geometry/land-cover layers remain available. New MapData instances
explicitly adopt the immutable catalog revision. New tiles do not silently
change a running snapshot. `BNGE_REGIONAL_PROVIDERS=off` disables regional and
national imagery, including direct colour-reference tile requests.

Runtime lookup uses NumPy and bounded local prepared tiles. Offline local
GeoTIFF preparation uses optional `server/requirements-preparation.txt`
(Rasterio 1.5.2); it preserves masks, CRS, raw units and checksums. It accepts
local GeoTIFF bytes, not VRTs which could fetch hidden network dependencies.
No REST service or raster reprojection runs in the frame loop.

- Layercake's actual pinned footer verifies the requested semantic columns and
  UTC timestamps. Normalization retains unknowns and ODbL lineage; explicit
  motor-vehicle restrictions affect navigation eligibility, not collision.
- GLC's publisher colormap has 30 classes, including **191 urban / 192 rural**.
  The sampled TIFFs declare no nodata tag. Unrecognized codes remain unknown.
  Actual W180 files use a positive wrapped longitude domain; preparation records
  the explicit longitude-origin shift. Canopy-model heights remain unchanged.
- SoilGrids sand/clay are g/kg and coarse fragments are cm³/dm³; all three
  normalize by 1,000, but mass and volume fractions are different quantities.
  Native raster nodata is −32768. The WCS sample omitted its nodata tag and its
  generic units conflict with the property documentation. Format/reprojection
  worked; scientific live mask validation remains pending. Do not interpret
  ambiguous service zeros as verified soil observations.
- JRC's selected occurrence raster covers March 1984–December 2024. The selected
  seasonality file is **annual 2024**, not an aggregate of the available
  2022–2024 asset years. Zero is valid; 255 is missing according to the guide,
  despite sampled TIFF headers omitting the tag. Current files use ZSTD, which
  the lightweight COG reader does not support; bounded local Rasterio
  preparation succeeded. Finer vector water geometry is preserved.

Exact inputs, live attempts, limits, tests and pending checks are recorded in
the [phase 04 handoff](../handoffs/04-global-data.md). HydroRIVERS remains
optional/unimplemented here. GlobalBuildingAtlas remains disabled pending its
separate license/quality decision. No LANDFIRE implementation exists in the
current source; absence is not a successful LANDFIRE ingestion test.
