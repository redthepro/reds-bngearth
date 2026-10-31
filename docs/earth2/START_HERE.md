# BeamNG Earth 2 — development starter kit

Prepared 2026-10-26 for a separate development session.

Build a faster worldwide Earth experience with richer free geographic data and a curated Sketchfab asset library. This package contains the design, source choices, implementation prompts, configuration templates, and setup helpers. It is **not an implemented replacement mod**.

The original project and the other open development session have not been changed.

## Start a new session

1. Open this folder as the new session's workspace.
2. Select **GPT-6.1 Sol / medium**. The project-local agent settings use this lead for every phase.
3. Paste the entire [kickoff prompt](prompts/00-kickoff.md). It sets up an isolated source copy and prepares the first implementation phase.
4. Run prompts 01–07 in order. Each explicitly spawns its phase team, integrates results, requires independent review and records a handoff.

Every prompt is now subagent-driven: **one Medium-effort lead plus up to three subagents**, with exact roles, ownership, dependencies and review gates. Ultra is not required. Project defaults are in [.codex/config.toml](.codex/config.toml); verify the actual session settings. See the [execution guide](docs/07-execution-guide.md).

| Prompt | Result | Recommended setting |
|---|---|---|
| [00 — Kickoff](prompts/00-kickoff.md) | Isolated source and implementation handoff | GPT-6.1 Sol / medium |
| [01 — Baseline](prompts/01-baseline.md) | Reproducible correctness and performance baseline | GPT-6.1 Sol / medium |
| [02 — World plan and cache](prompts/02-world-plan-cache.md) | Shared geometry decisions and reusable compiled cells | GPT-6.1 Sol / medium |
| [03 — Streaming](prompts/03-streaming.md) | Bounded scheduling and consistent world activation | GPT-6.1 Sol / medium |
| [04 — Global data](prompts/04-global-data.md) | Global road, vegetation, soil, and water adapters | GPT-6.1 Sol / medium |
| [05 — Sketchfab catalog](prompts/05-sketchfab-catalog.md) | Licensed, optimized, reusable assets | GPT-6.1 Sol / medium |
| [06 — Placement](prompts/06-semantic-placement.md) | Data-driven model selection and world placement | GPT-6.1 Sol / medium |
| [07 — Validation](prompts/07-validation-release.md) | Independent review, regression results, release candidate | GPT-6.1 Sol / medium |

## Read the design

- [Project brief](docs/01-project-brief.md)
- [Architecture](docs/02-architecture.md)
- [Free global data sources](docs/03-global-data-sources.md)
- [Performance and validation](docs/04-performance-and-validation.md)
- [Sketchfab and visual enrichment](docs/05-sketchfab.md)
- [Reviewed repository baseline](docs/06-repository-baseline.md)
- [Execution and agent configuration](docs/07-execution-guide.md)
- [MCP and plugin recommendations](docs/08-integrations.md)

## Local helpers

Run from this folder in PowerShell:

```powershell
# Optional manual bootstrap; prompt 00 can do this for you.
.\scripts\Bootstrap-Workspace.ps1

# Load the saved credential into this PowerShell process.
.\scripts\Use-SketchfabCredential.ps1

# Read-only authentication check; prints no account identity or token.
.\scripts\Test-SketchfabConnection.ps1
```

Bootstrap creates a new `source` directory from the reviewed commit. It refuses to overwrite an existing directory, does not install dependencies, does not start BeamNG, and does not push to GitHub.

Credentials are stored separately using Windows DPAPI under `%LOCALAPPDATA%\beamng-earth-2\secrets`. The encrypted credential works for this Windows user on this machine. If you move the package to another machine, run [Set-SketchfabCredential.ps1](scripts/Set-SketchfabCredential.ps1) there and enter the key privately.

Use `%LOCALAPPDATA%\beamng-earth-2\cache` for data and compiled caches. The Desktop is OneDrive-backed; keep large downloads, environments, and benchmark output out of its sync path. The JSON files under config are **design templates**, not configuration already understood by the existing mod. The .codex TOML files are actual project-scoped agent defaults.

See [packaging report](PACKAGING_REPORT.md) for what was actually checked.
