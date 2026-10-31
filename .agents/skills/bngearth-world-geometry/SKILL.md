---
name: bngearth-world-geometry
description: Improve BeamNG.Earth roads, bridge clearance, buildings and courtyards, scenery placement, landmark assets or scene lighting while preserving visual and collision consistency.
---

# Roads, buildings and scenery

Locate the defect in source data, generation, collision or game placement before applying a global visual offset.
Use a reproducible coordinate or synthetic fixture; a location-independent report can still become a geometry test.

## Code map

- Road profiles/surfaces: `server/grading.py`, `roads.py`, `roadmesh.py`, `surface.py`, `terrainpatch.py`.
- Props/signs/railways: `server/furniture.py`, `gasstations.py`, `rails.py`, `railways.py`.
- Buildings/assets: `server/buildings.py`, `landmarks.py`, `cdae.py`, plus the bundled mod assets and `CREDITS.md`.
- Game placement/lights: Lua `roads.lua`, `trees.lua`, `streetlights.lua`, `landmarks.lua`, `gasstations.lua`,
  `vienna_convention_speed_signs.lua` in the BeamNG.Earth extension directory.

## Geometry decisions

- Preserve grade and shared road endpoints while removing bumps. Keep the collision surface, pavement and lane paint
  aligned; smooth paint without shortcutting source bends or creating gaps at tile boundaries.
- Respect mapped width/lane counts before inferred building setbacks. Include divided roads, ramps, one-way streets
  and junctions when changing width estimation.
- Measure usable bridge clearance beneath slabs/girders using collision geometry. Keep approaches continuous and
  report constrained spans instead of creating vertical ramps or silently promising clearance everywhere.
- Place props against terrain at their actual positions, while preserving deck-supported props. Move lamp lights
  with their poles through rebases and remove paired sign/body/light objects together.
- Keep every multipolygon component and courtyard through lazy part lookup, LODs and collision generation.
  Rooftop-only parts must retain the parent's ground-level walls. A road inside a courtyard must not lift the building.
- Import assets only with usable licenses/attribution. Retain source/license entries in `CREDITS.md`, prepare bounded
  LODs/textures, and preserve mapped collision when custom visuals replace procedural buildings.

## Validate

Start with the affected tests: `test_grading`, `test_bridge_clearance`, `test_road_appearance`, `test_roadmesh`,
`test_roads`, `test_furniture`, `test_world_consistency`, `test_building_footprints`, `test_bundled_landmarks`.
Run matching Lua placement/lifecycle tests for game changes. Include neighboring tiles, a rebase and disposal.

Use the retained Pentagon fixture for parent walls/courtyards. For live comparison, distinguish render appearance
from physics: the game's static collision ray is stronger evidence of drivability than a render-only ray or screenshot.
Document locations and any remaining data limitations without claiming a model makes an entire metro area accurate.
