# 04 — Global semantic data adapters

Status: phase 04 standalone adapter/fallback gate passed with the live-data limitations below. Engine validation remains pending. Stop here; phase 05 is not started.

## Workspace and revision

Only this package and its isolated `source` checkout were changed. Starting/current HEAD: `aae4d6126d65a07315d0b28cc4a81dd68d86280a`, branch `earth2/development`. Earlier phases already had uncommitted changes; this phase leaves them intact. No commit, push, original-checkout operation, game installation change, Blender/game control or publication occurred.

Raw reports, acquired files, scripts, environments and benchmark outputs live under `%LOCALAPPDATA%\beamng-earth-2`. Phase evidence root: `benchmarks\04-global-data` (called `E` below). Stable source manifest `E\integration-freeze-3.json` covers 20 files, SHA256 `ac86943d239399cc071beaa7b738f6ee82cfbc20ba38f3d7527a48f9b4cfa40f`.

Dedicated Python 3.12.14: `%LOCALAPPDATA%\beamng-earth-2\venvs\py312\Scripts\python.exe`; NumPy 2.5.4, DuckDB 1.5.6, native earthfast VERSION 69. Rasterio 1.5.2 / GDAL 3.12.2 was installed only in that environment for offline preparation, declared in `source/server/requirements-preparation.txt`. Runtime lookup does not import GIS libraries.

## Changes

- `server/provider_contract.py`: shared masked samples/evidence and immutable prepared catalogs. Existing fetchers remain responsible for acquisition; existing compiled-cache locks coordinate preparation. Local TIFF preparation records actual SHA256, native/effective CRS, nodata, transforms, units, resolution, timestamps, license, confidence and library versions. Native IGH reprojection and explicit wrapped-longitude normalization are tested. Runtime uses bounded NumPy lookup only.
- Prepared work is limited to 262,144 queried points, 4,194,304 prepared pixels, 64 MiB memory LRU, 128 catalog entries, 1 MiB index, 64 MiB payload and 512 MiB/256 retained payload files. Oversized NPY headers are rejected before allocation; TIFF signatures/driver restrictions reject network-backed VRT inputs. Immutable payloads are retained for old catalog snapshots; preparation rejects growth beyond its disk cap.
- `server/osmtags.py`: actual Layercake schema supports tracktype, access, motor_vehicle, crossing and cycleway/left/right, plus bicycle/foot. Missing optional columns stay unknown. Retains ODbL, object ETag, schema-footer checksum, OSM record version and normalized UTC timestamp. Explicit motor restrictions affect navigation eligibility; road/collision geometry is unchanged.
- `server/glcfcs10.py`, `tools/prepare_glcfcs10.py`: verified 30-class GLC_FCS10 functional categories with pointwise WorldCover fallback and source indices. Needleleaf/broadleaf hints refine existing CHM tree groups; no species or height inference. Actual contributor metadata survives vegetation v15 Items, disk caches, slicing and credits. The credits accessor reads cached metadata without rebuilding vegetation or sampling providers.
- `server/soilgrids.py`, `server/jrcwater.py`, `tools/prepare_soilwater.py`: SoilGrids mean sand/clay/coarse-fragment topsoil fractions and independent validity masks; supplementary JRC occurrence fraction and annual seasonality months. No REST dependency, friction calibration, shoreline replacement or live water level.
- `server/mapdata.py`: opt-in semantic catalog via `BNGE_SEMANTIC_ROOT`, explicit adoption/invalidation by catalog hash, worker-side optional 64×64 evidence layers and bounded direct point queries. Semantic layers are not default layers. Transient corrupt-input fallback is not persisted, allowing recovery after repair. Existing fine water geometry is preserved byte for byte in integration tests.
- `server/imagery.py`: provider coverage/revision/quality metadata and `BNGE_REGIONAL_PROVIDERS=off` / `regional=False`. Regional and national imagery sources are excluded from ordinary and direct reference requests; the global fallback remains available. Mutable service URLs are explicitly not pinned revisions. LANDFIRE is absent from this checkout: no invented LANDFIRE test is claimed.
- `config/sources.json`, `docs/03-global-data-sources.md`, `source/CREDITS.md`: actual availability, release, attribution, uncertainty and operating instructions. HydroRIVERS remains optional/unimplemented; GlobalBuildingAtlas remains disabled.

## Bounded live evidence

No worldwide accuracy or completeness is inferred from these windows. Detailed URLs, acquisition times, ETags, checksums, native metadata and failed attempts are preserved in the referenced reports.

| Source | Release and observation | Result and limit |
|---|---|---|
| Layercake | Actual current footer: 36 columns; ETag `"ac4e0d5fc5b39ef28c01f3efebb51198-5536"`; footer SHA256 `8b5c25b8c3b7484bb4476227b051d881a6a9dbcfa1eb815af38abb061b6a3383` | Requested text fields and UTC microsecond timestamps verified. Only 16-byte header, 8-byte tail and 9,281,468-byte footer acquired from a 29 GB object. No live geographic row-ingestion claim; rich/legacy row queries use local Parquet fixtures. OSM/ODbL. |
| GLC_FCS10 | Zenodo 14729665 v1, map year 2023, CC BY 4.0 | One smallest 117,500,101-byte ZIP; SHA256 `60ad302c2e103fb828e2afed348826226db1417704a0902e5e854eaa55a4fae2`, publisher MD5 verified. Ten safely extracted TIFFs total 304,418,783 bytes. Atka and Wrangel 128×128 subsets each have 16,384 classified pixels. Actual TIFF nodata is unspecified; 0/255 remain unrecognized classes. W180 positive-longitude wrap initially yielded missing data, then succeeded with recorded transform correction. |
| SoilGrids | v2.0.0 native VRT snapshots: sand/clay RUN10, cfvo RUN06; 0–5 cm mean, 250 m IGH | Three 8×8 WCS samples succeeded; total discovery/sample traffic 48,468 bytes. Native WKT is used, never pseudo-EPSG 152160. Sand/clay g/kg and cfvo cm³/dm³ divide by 1000, retaining mass/volume distinction. Live TIFFs omit nodata metadata and contain ambiguous zeros: format/reprojection works, **scientific live mask validation remains pending**. Native VRT -32768 is preserved separately from the explicit effective override. No REST calls. CC BY 4.0. |
| JRC GSW | v1.5; occurrence March 1984–December 2024; selected seasonality is annual 2024 | Initial range attempt exposed ETag quoting, then actual compression 50000/ZSTD unsupported by the existing minimal reader. Preserved failure; offline Rasterio fallback decoded bounded subsets from two explicitly capped files. 28,426,577 + 7,296,379 = 35,722,956 acquired bytes, below amended 32 MiB/file and 40 MiB combined caps. Baltimore 128×128 subsets: occurrence 12,630 valid zeros / 3,754 positive; seasonality 12,968 zeros / 3,416 positive. 255 masking is fixture-tested; absent in these windows. EC JRC/Google attribution and Pekel et al. citation retained. |

JRC source SHA256: occurrence `a9a31faa6f7301b8b10eb8398d698521741f27c2271e718120ca56d5edb6f5c4`; seasonality `23d27ca9282568a66bef23c61758936fb951af3beee75c8de187c884a9a1a822`. The 2022–2024 download collection contains separate annual seasonality files; the selected 2024 file is not a three-year aggregate. Collection 1/2 co-registration offsets can reach or exceed one 30 m pixel.

Primary sources: [Layercake schema](https://layercake.openstreetmap.us/layers/highways/), [GLC release](https://zenodo.org/records/14729665), [SoilGrids FAQ](https://docs.isric.org/globaldata/soilgrids/SoilGrids_faqs_01.html), [SoilGrids WCS](https://docs.isric.org/globaldata/soilgrids/wcs.html), [JRC download](https://global-surface-water.appspot.com/download), [JRC v5 guide](https://storage.googleapis.com/water-world/downloads_ancillary/DataUsersGuidev2024_v.5.pdf) (seasonality pp.6–7). Context7 lookups used Rasterio `/rasterio/rasterio/1.5.2`, NumPy `/numpy/numpy` and `/websites/numpy_doc`, DuckDB `/websites/duckdb_current`, GDAL `/osgeo/gdal` and CPython. Indexed NumPy versions differ from installed 2.5.4; actual installed behavior was tested.

## Verification and reproduction

Run from the isolated `source` directory. Ordinary regressions are offline, with dedicated temporary storage:

```powershell
$python = "$env:LOCALAPPDATA\beamng-earth-2\venvs\py312\Scripts\python.exe"
$evidence = "$env:LOCALAPPDATA\beamng-earth-2\benchmarks\04-global-data"
$env:BNGE_TEST_OFFLINE='1'; $env:BNGE_SKIP_DRIVE='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:TEMP="$env:LOCALAPPDATA\beamng-earth-2\runtime\tmp"; $env:TMP=$env:TEMP
& $python -B "$evidence\validation\run_tests.py" --label new-local-check test_provider_contract test_global_data test_glcfcs10 test_soilgrids test_jrcwater
& $python -B "$evidence\validation\run_tests.py" --label regressions-local-check test_osmtags test_mapdata test_vegetation test_imagery test_surface test_navgraph test_roads test_roadmesh test_grading test_world_consistency test_worldplan test_server_generations test_worker_generations test_terrainprep test_compiledcache test_blockworker test_streaming_lifecycle test_streaming test_netfetch test_protocol test_protocol_agreement
git diff --check
```

Use fresh labels: the harness refuses to overwrite evidence. Before the attribution fix, independent checks passed all 49 new tests and 440 executed regression cases across 21 modules; four expected skips (three live imagery tests offline, one LuaJIT-specific case unavailable). Four additional independent adversarial probes passed: snapshot immutability, boolean schema rejection, 160 MB declared-array allocation rejected before deserialization, and network-backed VRT rejected with zero loopback requests. Source fingerprints remained unchanged during each run. The attribution fix is independently rechecked below; passing prior-phase engine checks are not invented or counted here.

The lead initially launched the credits suite without the two offline flags, then stopped its exact identified processes and restarted offline. That interrupted attempt is not a passing test result.

## Agent ledger and resolved findings

The host advertises fixed role settings matching the requested files. Lead requested/default configuration is GPT-6.1 Sol/medium; the host exposes no introspection/switch for the already active lead, so its actual model/effort is unverified. At most three subagents plus lead ran concurrently; no recursive agents, concurrent heavy benchmarks or agent Git mutations.

| Actual task ID | Requested / known host role setting | Ownership and contribution |
|---|---|---|
| `/root/provider_map` | earth_explorer, Sol/medium | Read-only schema/release/license discovery and bounded metadata evidence; `E\provider-map.md`, `metadata/`. Verified actual GLC legend, Layercake fields and timestamps, Soil CRS/units, JRC release. |
| `/root/vegetation_adapter` | earth_specialist, Sol/high | `glcfcs10.py`, its 15 tests, legend fixture and preparation CLI; live GLC sample. `E\vegetation-adapter-report.md`. Released files before integration review. |
| `/root/soil_water_adapters` | earth_specialist, Sol/high | `soilgrids.py`, `jrcwater.py`, their 15 tests, `prepare_soilwater.py`; bounded source evidence. `E\soil-water\implementation-handoff.md` and acquisition ledgers. |
| `/root/global_validation` | earth_validator, Sol/high | Independent stable-patch review, offline tests, adversarial probes, source hashes, artifact audit and sole serial measurement owner. Read-only source; external outputs in `E\validation`. |
| `/root` | requested Sol/medium, actual active setting unverified | Shared schemas/preparer, integration, remaining tests, configuration, credits, docs and this handoff. Sole Git coordinator; no Git mutations performed. |

Lead review resolved mismatched categorical-unit acceptance, transient-cache repair, UTC timestamp conversion, and wrapped GLC longitude preparation. Independent review found that actual rendered vegetation omitted GLC source records from its cache and credits. Source edits paused until regression completion; vegetation v15 metadata persistence/accessor and visible-vegetation credits fixed it. `validation\vegetation-lineage-freeze2\results.json` independently verifies disk reload, metadata isolation, exact source attribution and zero provider/build calls in credits. The original demonstrated failure remains in `vegetation-lineage-freeze1`.

The freeze2 affected run passed integration 10, vegetation 13 and MapData 61 cases, but credits had 25/26 pass: its existing session throttle case observed a 0.235 s send gap versus 0.2375 s tolerance. An isolated rerun passed (10.239 s), and the lead's full 26-case run passed (90.536 s); the failed run is retained rather than erased. Inspection identified a real timing weakness: spacing check starts allows variable attribution computation to shorten send intervals. The bounded freeze3 fix timestamps completion in `Tracker.check`'s `finally` path, including unchanged/failed checks, without weakening tolerance. An injected-clock regression covers computation without wall-clock noise. Independent review also records the platform's 15.625 ms monotonic resolution, which exceeds the session test's 12.5 ms slack; final results distinguish deterministic correctness from timing observations.

Independent artifact audit rehashed the actual GLC archive, extracted files and both JRC TIFFs, and checked safe archive paths and duplicate copies without network access: `validation\local-artifact-audit.json`. Duplicate local JRC response copies are not counted as additional acquisition bytes.

## Final measurements and remaining gate

Final freeze3 independent checks: **11/11 integration tests and 26/26 credits tests pass**, source fingerprints unchanged; the credits module took 91.85 s outer wall time. The deterministic completion probe passes changed, unchanged and error paths. Freeze2's unchanged vegetation 13/13 and MapData 61/61 remain valid. Combined with the original new-module and regression runs, this establishes the final behavior without counting reruns as distinct coverage or hiding the preserved intermediate timing failure. Reports: `validation/affected-freeze3/results.json`, `validation/credits-completion-freeze3/results.json`, and `validation/review-final.md` (final source verification: `validation/verified-freeze3-final.json`).

Exact additional commands from isolated `source`, using the environment above:

```powershell
& $python -B "$evidence\validation\run_tests.py" --label affected-freeze3 test_global_data test_credits
& $python -B "$evidence\validation\run_measurements.py" --label measurements-freeze3
```

These recorded labels already exist; use new labels when reproducing. Independent probes and their exact argv are retained beside their results. Serial benchmark raw samples: `validation/measurements-freeze3/results.json`; hardware: `validation/hardware.json`. CPU **Ryzen 7 7700X**, 8 cores/16 threads, 50,587,820,032 bytes physical RAM, Windows, dedicated Python environment above. No GPU or game benchmark; game version/settings are not applicable because no game was run. Cold means a fresh Python catalog/object, not an OS-cache flush. All provider data/transport in the measured matrix is synthetic; **zero real provider requests**. Tiny 2×2 per-provider tiles exercise lookup overhead, not realistic world-raster throughput.

| Serial workload | Fresh-object median | Reused-object median | Interpretation |
|---|---:|---:|---|
| Combined GLC + three soil + two JRC layers, 4,096 points, empty catalog, four regions | 0.413–0.455 ms | 0.278–0.283 ms | All 4,096 vegetation points fall back to WorldCover; soil/water remain masked missing. |
| Maryland-only catalog, Maryland | 5.248 ms | 0.769 ms | All points use prepared facts; occurrence zero remains valid. |
| Maryland-only catalog, Kenya/Chile/Wrangel | 0.826–0.995 ms | 0.333–0.339 ms | No false regional coverage; vegetation fallback and missing soil/water. |
| Four-region prepared fixtures | 5.341–5.704 ms | 0.892–0.904 ms | All points valid; three fresh-object trials and 15 warm trials per case. |
| Vegetation baseline, 2,140 items | 83.777 ms build | 10.486 ms disk reload | Five trials each. |
| Same vegetation with GLC refinement | 86.527 ms build | 11.143 ms disk reload | Same count and byte-identical height/position/radius/rank/yaw/scale/kind; only groups differ. |

One fresh Maryland lookup trial took **36.571 ms**; medians above do not hide that outlier. The observed GLC build increment is 2.750 ms in this fixture, not a general speedup or frame-time claim. In-memory vegetation returns were below 0.004 ms in 20 trials per case and are not meaningful engine measurements.

Eight synthetic imagery cases compare enabled/disabled regional/national providers across Maryland, Kenya, Chile and Wrangel. Disabled cases contact **only EOX**, with 1–2 synthetic requests versus 3–4 when the enabled fixture selects USGS. Fresh render: disabled 4.448–5.405 ms, enabled 10.492–22.103 ms; warm medians disabled 0.627–0.850 ms, enabled 0.616–0.794 ms. The fake provider pixels are not necessarily equal and these numbers do not establish real source availability or network speed.

Offline preparation of a 256×256 EPSG:3857 TIFF to 128×128 EPSG:4326 took median **6.36 ms** over three trials (6.19–13.40 ms); source 131,550 bytes, prepared payload 3,074 bytes. Source SHA and preparation metadata are in the report. This includes actual Rasterio/GDAL work but is an analytic small-file observation. The full bounded matrix completed in 3.49 s outer wall time; no other benchmark overlapped it, and source hashes stayed unchanged.

No frame-time, engine collision, real global accuracy or speedup claim follows from these Python fixtures. Phase 03 engine/retained-memory checks remain pending. SoilGrids live scientific mask interpretation remains pending even when transport and reprojection succeed.

Next action: review the final independent results and the explicit live-data limitations here; keep phase 05 stopped until the user selects it. Before enabling unverified SoilGrids source windows, resolve provider nodata semantics against a scientifically interpretable reference window.
