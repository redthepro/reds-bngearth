# Sketchfab: data-driven visual enrichment

## Intended use

World data selects what kind of object fits a location. A local catalog supplies a model that fits that meaning, region, scale, and budget. Reuse the same prepared model across many placements with deterministic variation.

Sketchfab is not a worldwide georeferenced model dataset. A model name or search result is not sufficient evidence of its real-world position.

| Geographic evidence | Visual enrichment | Constraint |
|---|---|---|
| Road class, width, surface, access, bridge tags | Guardrails, bollards, barriers, suitable road furniture | Avoid obstructing lanes; do not invent traffic-control facts |
| Land-cover type + canopy height | Broadleaf/needleleaf tree and shrub families | Functional type is not exact species |
| Rock/bare-ground class + terrain slope | Reusable rock clusters | Seeded density; avoid roadway clearance |
| Building use and dimensions, when known | Roof equipment, entrances, industrial details | Fit existing footprint/height; no arbitrary landmark substitution |
| Water boundary and compatible land use | Limited shoreline vegetation/props | Do not infer docks or infrastructure as factual |
| Verified named landmark and explicit transform | Specific landmark model | Curated location, orientation, dimensions, provenance |

Broad land-cover does not justify placing arbitrary street furniture. Treat decoration inferred from sparse data as procedural visual enrichment, with conservative defaults.

## Developer pipeline

1. Search metadata for a small category-based shortlist.
2. Check model UID, creator, source URL, downloadable status, license, attribution, and intended use.
3. Download through the supported API using the developer's credential.
4. Hash input, extract safely, reject path traversal and excessive archive expansion, and validate referenced textures.
5. Normalize scale, axis convention, pivot, winding, and materials.
6. Generate LODs, texture sizes, material reuse, and simple collision proxies where required.
7. Convert to an engine format proven by a current-version probe.
8. Save a manifest and attribution record, then add the asset to an approved local catalog.
9. Compile deterministic placements using geographic rules.
10. Stream only prepared local artifacts during play.

The public Download API documents glTF/GLB and USDZ rather than original uploaded source formats. Download links are temporary; retain the source UID and content checksum, not an expiring URL as the asset identity. [Download API](https://sketchfab.com/developers/download-api), [download flow](https://sketchfab.com/developers/download-api/downloading-models).

## Authentication and licensing

The saved personal key is for development tooling. The helper verifies the Data API profile endpoint using token authentication. Passing that check does not prove that every model is downloadable or that a particular Download API authentication flow is available.

A public integration must use the supported end-user authentication flow; do not distribute the owner's token. The download documentation uses OAuth bearer tokens. Confirm the supported flow before implementing downloads rather than assuming a personal Data API token is interchangeable. [Data API](https://sketchfab.com/developers/data-api/v3), [authentication schema](https://docs.sketchfab.com/data-api/v3/swagger.json), [integration guidelines](https://sketchfab.com/developers/download-api/guidelines).

Default catalog policy: admit verified CC0 and CC BY assets after metadata and use checks. Route share-alike and noncommercial assets to a separate eligibility decision; exclude no-derivatives or unknown licenses from the conversion pipeline. Save creator, license URL/version, original page, acquisition date, changes, and generated attribution. Asset-specific rights still apply. Do not treat all free downloads as freely redistributable. Follow the publisher's credit requirements through derivatives.

## Performance policy

Prepare models before gameplay. No network search, archive extraction, texture baking, decimation, or conversion in the frame loop.

Budgets are category- and distance-specific: triangles per LOD, material slots, texture dimensions/bytes, collision complexity, instance count, and draw calls. Use the manifest template as a starting structure; phase 01 measurements determine numeric defaults.

Repeated assets should share mesh/material/texture resources where the engine supports it. Prefer fewer material slots and predictable texture residency. Small scenery is usually render-only. Trees/guardrails need deliberately designed collision behavior, not a detailed visual mesh automatically marked collidable.

## Runtime matching

Filter eligible assets by category, region/climate applicability, dimensions, license profile, supported LOD, and material/geometry budget. Select deterministically from stable feature ID + catalog version + world seed. Persist the selected asset UID and transform in the compiled cell.

Use stable feature ownership and exclusion zones around roads, intersections, buildings, and water. Preserve gameplay support when a visual model is unavailable. Every category has a procedural fallback.

A local rule should be able to explain: source feature, normalized meaning, chosen asset, selection rule/version, transform, and budget. This makes misplaced objects debuggable.
