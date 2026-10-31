# MCP and plugin choices

Checked 2026-10-26. Codex handles subagent coordination; integrations give those agents useful tools and data. None of these needs to run inside the shipped mod.

## Recommended setup

| Integration | Status in this session | Value for this project |
|---|---|---|
| GitHub connector / official GitHub MCP | GitHub connector already available and used | Read source, review diffs, inspect CI and track upstream changes |
| MCP for Blender | Tools exposed; live Blender connection not probed | Inspect/prepare Sketchfab assets, check scale/materials and develop conversion recipes |
| Context7 MCP | User installed it; local configuration and live MCP documentation calls verified | Current library/SDK/API documentation for dependency work |
| BeamNG built-in MCP | Not connected or enabled by this kit | Future engine probes, controlled regression runs and visual checks |

### GitHub

Use the existing connector for repository/CI context; use local Git for the isolated checkout. No need to install a second overlapping GitHub integration. GitHub also publishes its [official MCP server](https://github.com/github/github-mcp-server). Repository writes and publication remain outside these prompts unless separately authorized.

### Blender

The available integration is the community [MCP for Blender](https://github.com/ahujasid/mcp-for-blender), not an official Blender Foundation plugin. It supports scene control, Python, materials and asset libraries including Sketchfab and Poly Haven.

Use it during phases 05–06 to inspect sample assets and develop repeatable preparation recipes. Once a recipe works, turn it into versioned Blender/Python batch processing rather than relying on a chat to rebuild every asset.

One worker owns the live scene. Other agents inspect exported metadata or files. Use a dedicated scene and saved inputs; do not take over an unrelated active Blender session. Optional paid generators/premium services are not required.

### Context7

The user's configured HTTP MCP endpoint successfully handled initialization, tool discovery, library resolution and two documentation queries during this update. The selected Codex reference was `/llmstxt/learn_chatgpt_llms-full_txt`, an index of official documentation; it confirmed the subagent configuration keys and standalone role-file format.

Every agent follows the supplied Context7 policy in AGENTS.md: resolve the library, choose a relevant/versioned ID, query one concept, and use the returned evidence. It helps with library APIs; it is not a source of truth for geographic dataset licenses or actual BeamNG behavior.

The publisher documents a free tier with usage limits; no paid plan is required by this kit. [Context7 documentation](https://context7.com/docs/overview). Existing credentials stay in the user's configured MCP connection and are not copied into project files.

### BeamNG

BeamNG documents an MCP server in its v0.39 tech release, enabled in Options > Advanced, with a local HTTP endpoint. The reviewed project also records MCP-based probes. Confirm the actual BeamNG.drive version and exposed tools before relying on specific capabilities. [Official release notes](https://beamng.tech/blog/beamng-tech-039/).

Use an isolated authorized test session. The current other development/game session must not be interrupted. Keep a single engine-test owner and inspect the server's tools before issuing commands. This kit does not start the game or change its settings.

## What I would avoid adding now

No separate agent-orchestrator MCP is needed for this workflow. Native subagents and the project role files already provide the coordination requested.

Geospatial ingestion should remain a reproducible code pipeline using the dataset providers, local tools and tests. Add a specialized GIS/STAC integration only when a concrete discovery/inspection gap appears. More overlapping tools do not automatically improve performance or accuracy.

No additional plugins were installed by this update. The user installed Context7; its existing connection was tested.
