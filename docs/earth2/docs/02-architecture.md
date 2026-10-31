# Architecture proposal

## Keep the three-language boundary

Python coordinates providers, normalized facts, dependencies, caching, and scheduling. Rust handles measured bulk numeric or geometry bottlenecks through coarse calls. Lua owns engine integration and per-frame installation budgets.

Do not move everything to Rust or rebuild the entire mod before measuring. Existing protocol and engine constraints matter more than language fashion.

## Proposed pipeline

```text
versioned source records
        |
normalized facts + provenance
        |
resolved shared world plan (stable geographic cells)
        |
        +-- terrain / road visual geometry
        +-- collision support
        +-- navigation
        +-- materials / vegetation
        +-- semantic asset placements
        |
content-addressed compiled artifacts
        |
bounded streaming + engine installation
        |
activate compatible collision / navigation / visuals
```

### 1. Normalized facts

Provider adapters expose coverage, revision, CRS, vertical datum, units, resolution, confidence, license, and missing-data semantics. Preserve source identifiers and revision lineage. Select providers by coverage and usefulness for each property, not merely pixel size.

Use global land-cover and vegetation types independent of regional classification systems. Soil fractions remain physical observations/predictions; material friction is a separately calibrated gameplay mapping.

### 2. Shared world plan

Resolve road centerlines, width, grading, intersections, bridge/tunnel vertical profiles, terrain edits, and water interactions once. Collision, visuals, and navigation consume those decisions.

Use stable world-cell coordinates and explicit transforms into the current anchor. Separate canonical geometry from anchor-specific payloads. Rebasing may invalidate a transform or payload; it should not require re-running geographic interpretation.

Cell borders require a deterministic ownership rule and a dependency halo. Boundary features have stable IDs; neighbor evaluation sees compatible revisions. Specify coordinate/height tolerances before changing geometry.

### 3. Artifact cache

Key artifacts using canonical source revisions, world-plan version, processing settings, schema version, compiler version, and LOD. Include quality/completeness state. Distinguish immutable source snapshots from mutable endpoint URLs.

The existing road-plan cache deliberately avoids persisting incomplete plans across runs. Do not remove this protection without dependency tracking and invalidation. A provisional DEM result must be upgraded when a higher-quality dependency arrives.

Store manifests and payloads atomically; reject truncated or checksum-invalid entries. Separate raw-source cache, normalized facts, compiled world artifacts, and anchor payload cache. Evict by byte budgets and measured reuse, while pinning active dependencies.

### 4. Scheduler

Assign work deadlines based on predicted arrival time and safe support distance. Give collision/drivable terrain reserved capacity. Then schedule near-road visuals, textures, and far scenery. Retain concurrency separation wherever it prevents starvation.

Use bounded queues, per-provider limits, in-flight de-duplication, download byte budgets, retry/backoff, cooperative cancellation between expensive stages, and backpressure from the game. A cancelled future may still be running; workers must check generation/request validity.

### 5. Activation

Make lifecycle states explicit: requested, building, prepared, installed, activated, retiring, released. Carry request, anchor, cell, and world-plan revision IDs. Reuse the current prepare/commit and release acknowledgments.

Do not expose navigation across a bridge before compatible collision support exists. Cosmetic texture arrival can remain independent. Keep old compatible support until replacement activation succeeds, within the memory budget. Reject stale completion messages and make duplicate messages harmless.

### 6. Visual enrichment

Compile data-derived placements against a local catalog. Share materials and textures where possible; use engine-supported instancing only after checking actual APIs. Never assume engine object creation, collision rebuilds, or arbitrary meshes can be moved off the main thread.

Use simplified collision only where gameplay needs it. Far scenery, small decoration, and distant landmarks usually need visual LODs without collision. Keep collidable geometry focused near the drivable corridor.

## Migration strategy

First extract a common terrain preparation path while preserving outputs. Then introduce stable plan/artifact contracts and cache migration. Change scheduling and activation behind switches. Add global providers through those contracts. Introduce the asset catalog last so geometry and streaming regressions can be isolated.

Keep the old path usable until representative parity and performance checks pass. Avoid permanent dual pipelines after migration is validated.

This is a proposed design, not a claim that these components are all absent. See the [baseline](06-repository-baseline.md).
