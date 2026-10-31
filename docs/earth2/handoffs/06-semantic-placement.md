# Phase 06 — Semantic placement handoff

## Status

**Acceptance pending.** This phase added and independently reviewed a deterministic semantic placement resolver, but it is not yet wired to production normalized facts, the compiled world-plan cache, or a rendering path. The three slices are tested rule contracts, not end-to-end working data-to-asset flows. No approved prepared catalog assets exist from phase 05, so fallback-only behavior is correct and visual improvement cannot be demonstrated.

## Changes

- `source/server/semanticplacement.py`: standalone rules for explicit roadside barriers, broadleaf/needleleaf tree families, and terrain rocks; evidence/clearance/dimension/licensing checks; stable deterministic choice; procedural fallback explanations; bounded resources; no collision contribution.
- `source/server/tests/test_semanticplacement.py`: focused deterministic, eligibility, clearance, fallback, and resource-cap coverage.
- Updated project execution guide to use GPT-6 Luna / medium consistently, matching the available host model and the user's override.

## Verification

- `python -m unittest server.tests.test_semanticplacement -v` — 20 tests passed (author run and independent reviewer run).
- `python -m py_compile server/semanticplacement.py` — passed.
- Independent reviewer found no demonstrated resolver logic defect; identified the production integration/cache/runtime gap described above.
- BeamNG documentation was checked for static instancing formats: Forest is documented for repeated trees/rocks and spatial batching/culling, while TSStatic is for individual static meshes and static manager optimization. These docs do not verify a gameplay-time dynamic custom-asset instancing API, and no engine session was available.

## Evidence and measurements

- No BeamNG runtime validation, visual captures, frame-time or memory comparison was performed for this phase.
- No isolated game session was available. The only prepared asset state remains the phase-05 handoff: none approved; T-Bollard remains unprepared due conversion/material constraints.
- No speedup or asset visual improvement is claimed.

## Agent ledger

- `/root/placement_evidence` — `earth_explorer`, requested GPT-6 Luna / medium; read-only source/fact mapping; output at `%LOCALAPPDATA%\\beamng-earth-2\\benchmarks\\06-semantic-placement\\evidence\\report.md`. Confirmed conservative evidence policy and data gaps.
- `/root/placement_impl` — `earth_specialist`, requested GPT-6 Luna / medium; owned `source/server/semanticplacement.py` and `source/server/tests/test_semanticplacement.py`; implemented resolver and 20 tests.
- `/root/placement_review` — `earth_reviewer`, GPT-6 Luna / medium; no edits; independently reran tests and reviewed stable patch. Found integration/cache/runtime evidence gaps; no demonstrated logic defect. Findings are retained as acceptance blockers, not silently waived.
- Lead `/root` integrated/reviewed the contract and wrote this handoff. Project settings specify GPT-6 Luna / medium. The host model is `gpt-6-luna`; `gpt-6.1-luna` is not exposed.

## Next action

Wire a conservative candidate builder from normalized `MapData`, cache placement plans alongside the compiled world plan with catalog/rule/seed identity, and connect only documented engine-supported representation. Preserve fallback and ownership semantics. Then test neighboring cells/rebases/catalog changes/crowded scenes and capture isolated-engine resource/performance/visual evidence once an approved asset and isolated game session are available.
