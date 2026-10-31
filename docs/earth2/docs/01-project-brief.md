# Project brief

## Desired outcome

A global BeamNG Earth environment that becomes drivable quickly, stays ahead of the vehicle, and gets visually richer without putting more work on the game's frame loop.

Preserve the existing project's useful implementation. Build an isolated successor incrementally, with comparison points and rollback paths.

## Global by design

A location should work with worldwide base data even when no state, country, or municipal feed exists. Regional lidar, imagery, vegetation, and building feeds are optional improvements selected through provider coverage and quality metadata. Maryland imagery and LANDFIRE-style regional classifications must not define the global material or vegetation model.

Use common semantic concepts: road surface and access, vegetation functional type, canopy structure, soil composition, water persistence, building use, and verified landmark identity. Providers translate into those concepts. The rest of the renderer should not contain region-specific provider logic.

## What to improve first

1. Establish the real performance baseline and current correctness.
2. Eliminate duplicated terrain/road decisions and repeated compilation.
3. Make CPU, download, memory, and engine-install work bounded and prioritized.
4. Extend the existing global road data.
5. Improve vegetation, ground materials, and water with free global datasets.
6. Use a curated Sketchfab catalog to replace suitable generic visuals.
7. Validate globally, including sparse data and provider outages.

## Sketchfab's role

Geographic datasets describe a place. A curated model catalog supplies visual representations: street furniture, guardrails, vegetation, rocks, industrial props, building details, and verified landmarks.

Do not search Sketchfab separately for every tree or road. Prepare a small reusable collection, classify it, optimize it, and map geographic features to that collection deterministically. A plausible asset is visual synthesis, not evidence that the real object exists there.

## Definition of success

- Improved measured time to drivability and frame-time behavior on the same routes/settings.
- No regressions in road support, bridge transitions, seams, rebase behavior, or navigation.
- Useful output on several continents with regional feeds disabled.
- Missing optional sources degrade gracefully.
- Cached areas survive restarts without repeated expensive compilation when inputs are unchanged.
- Models and textures stay within explicit budgets, with reliable procedural fallbacks.
- Source and model attribution remain traceable.

Free means no mandatory paid data subscription. Storage, bandwidth, conversion time, and development-model usage still have costs. This kit does not assume a free production CDN.
