# 05 — Curated Sketchfab asset preparation

Status: **pipeline implemented and independently checked; sample-catalog acceptance gate remains pending.** The only acquired live model (T-Bollard) is safely rejected during conversion because of nonuniform vertex colors. It is not runtime-approved. No engine checks were run.

## Workspace and revision

Only this package and its isolated `source` checkout were used. Starting/current source HEAD: `aae4d6126d65a07315d0b28cc4a81dd68d86280a`, branch `earth2/development`. The checkout already contained earlier-phase changes; no commit, rebase or push was made. The original checkout, installed game and active game session were not touched.

Source changes for this phase are in `source/server/assetcatalog.py`, `source/tools/sketchfab_acquire.py`, `source/tools/prepare_asset.py`, `source/tools/blender_asset.py`, and `source/server/tests/test_assetcatalog.py`. The lead also updated the shared manifest and budget contracts, `config/sources.json`, and the phase/future-agent settings. All acquisition, conversion and benchmark material stays under `%LOCALAPPDATA%\\beamng-earth-2`.

The example manifest schema is v2 and requires model/creator provenance, structured license evidence, archive hash and size, conversion metadata, physical dimensions, artifact hashes, output budgets, quality flags and explicit runtime approval. Unknown dimensions/units, unsupported licenses and incomplete provenance do not become zeroes or implicit approvals. Runtime lookup uses a local prepared-artifact root and procedural fallback; it does not download models. The acquisition budget is 12 candidates/250,000,000 actual archive bytes total, at most 64 MiB compressed and 512 MiB expanded per model, 2,048 archive members, compression ratio 100, three redirects, 30-second request timeout and 300-second total deadline. Engine budgets remain pending.

## Implementation and evidence

- `assetcatalog.py` performs schema-v2 admission, strict metadata/type checks, CC0/CC BY evidence gating, catalog hashing, attribution generation, safe archive/resource checks and verification of artifact hashes, dimensions, PNGs, materials and geometry budgets. Removed or stale artifact entries are skipped to permit procedural fallback.
- `sketchfab_acquire.py` has explicit developer API-token and OAuth modes, rechecks public model/license/creator data against the approved manifest, downloads bounded archives with resumable/hash-checked accounting, restricts archive hosts/redirects, and does not forward credentials to signed archive URLs. Invalid nested metadata and license URLs fail closed. Current public model and creator names must match the manifest.
- `prepare_asset.py` retains originals separately, validates extraction receipts and local resources, runs Blender in an isolated subprocess, and writes manifest/attribution plus DAE LODs, texture/material and simple collision outputs where supported.
- `blender_asset.py` normalizes imported geometry to meter units, +Z up/+Y forward/right-handed axes and ground-center pivot; it handles mirrored winding/normals and bounded LOD decimation. Uniform vertex tint is supported. Nonuniform `COLOR_0` fails closed because there is no supported deterministic bake recipe in this pipeline.

The metadata agent recorded 12 unique candidates (four roadside, three vegetation, three rocks and two building details). Nine had structured CC BY 4.0; three were excluded by the default policy. Public metadata did not establish dimensions, source units, archive sizes or region/climate applicability. The shortlisted T-Bollard is CC BY 4.0 and its source archive was acquired once. The lead's read-only `/download` capability probe returned HTTP 200 with the personal Token scheme; a separate source review documented OAuth bearer use in the dedicated Download guide. These results apply to this capability probe and do not establish general OAuth/API-token equivalence. No signed URL or credential was recorded in the handoff.

The acquired T-Bollard archive is 2,431,656 bytes, SHA256 `6d79c958e458b35299b89a8f2280fc32a6d0ef41d53be142227bdfd032c3a9f0`. It is preserved at `%LOCALAPPDATA%\\beamng-earth-2\\benchmarks\\05-sketchfab\\pipeline\\t-bollard-acquisition`. Run 01 rejected its `KHR_materials_pbrSpecularGlossiness` before that supported extension was narrowly admitted based on the installed Blender importer. Run 02 then imported the asset and rejected nonuniform `COLOR_0`; specialist run 03 reproduced the same controlled rejection using Blender 5.2.2 LTS. No asset was marked prepared or approved, and no additional model archive was downloaded.

An authored CC0 analytic fixture (not a Sketchfab asset and not distributable) was converted earlier in the pipeline using Blender 5.2.2 LTS. It measured dimensions `[2,4,3]` m, LOD counts `12/6/4` triangles and 12 collision triangles. Its complete preparation run took 0.898 seconds; a prior cold-process run took 4.645 seconds. These are two fixture observations before later material/tint and hardening edits, include uncontrolled process/cache effects, and do not establish final-code performance or a speedup. Fixture conversion does not satisfy the live sample-catalog gate.

## Verification

The implementation agent and independent validator both ran these commands from `source` after the final attribution fixes:

```powershell
python -m unittest server.tests.test_assetcatalog -v
python -m py_compile server\assetcatalog.py tools\sketchfab_acquire.py tools\prepare_asset.py tools\blender_asset.py
```

Result: **22/22 focused tests passed** (independent validator: Python 3.14.7, 0.133 seconds); `py_compile` passed without diagnostics. New cases cover stale model and creator names plus non-string license URLs. Independent review found those two issues before the fix; both were resolved and the validator rechecked the stable source. `git -C source diff --check` exited 0; Git emitted only existing CRLF normalization warnings for three unrelated earlier-phase files.

No live-game import, material resolution, LOD switch, collision behavior, frame-time, RAM or VRAM validation was performed. Metadata/API behavior was probed separately, but the independent validator did not run a live API check. Blender's real-asset conversion was tested by the specialist; its result is a controlled rejection described above, not a success.

## Agent ledger

| Task ID | Role; requested and known actual settings | Ownership/output | Review result |
|---|---|---|---|
| `/root/catalog_evidence` | `earth_explorer`; requested and role-reported GPT-6.1 Sol/medium | Read-only public metadata shortlist, candidate/schema evidence under `benchmarks/05-sketchfab/catalog-evidence` | 12-candidate evidence; no model downloads. |
| `/root/catalog_pipeline` | `earth_worker`; requested and role-reported GPT-6.1 Sol/medium | Initial ownership of the five pipeline source/test files; implementation handoff under `benchmarks/05-sketchfab/pipeline` | 18 tests at initial release, authored fixture conversion and one authorized download. Later specialist owned same assigned files; prior work was preserved. |
| `/root/catalog_conversion` | `earth_specialist`; initially GPT-6.1 Sol/high | Interrupted at the user's explicit model override; its shared-checkout work was preserved | Replaced before claiming completion; no settings switch was possible in-place. |
| `/root/catalog_conversion_luna` | `earth_specialist`; explicitly started as GPT-6 Luna/medium (the host does not expose `gpt-6.1-luna`) | Hardened catalog/acquisition validation in its assigned files; reproduced run-03 T-Bollard rejection | Fixed the independent review's stale-name and malformed-license-URL findings; 22 tests and py_compile passed. |
| `/root/catalog_final_validation` | `earth_validator`; explicitly started and rerun as GPT-6 Luna/medium | No source edits; independently reviewed the stable patch and ran focused verification | Confirmed both findings resolved; 22 tests, py_compile passed, no additional correctness issue found. |

Project-local lead/subagent defaults and every `.codex/agents` role are now `gpt-6-luna`/medium. Prompts 05-07 were updated to avoid overriding those defaults on future phases. The exact identifier `gpt-6.1-luna` is not available in this host's supported model list. These project settings apply when loaded by the client; an already-running lead session cannot be hot-switched through the agent tools.

## Acceptance and next action

The fail-closed offline pipeline, attribution path, archive limits and fixture tests are present, but **the acceptance gate is not passed**: no eligible Sketchfab asset reached a validated optimized manifest. T-Bollard lacks verified physical dimensions and cannot preserve nonuniform vertex colors through the supported conversion recipe. Keep it `acquired_not_prepared`, with procedural fallback. Next phase-05 action is to use an already authorized eligible asset with supported materials/colors and verified scale, or implement and test an approved deterministic vertex-color bake, then produce one approved local sample and run engine import/LOD/collision checks. Do not infer a game performance result from the fixture timings.
