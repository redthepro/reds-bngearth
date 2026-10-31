# Subagent execution and preflight

## Medium lead, explicit delegation

All eight phases require actual subagent work. Open the package itself as the workspace, select **GPT-6 Luna / medium**, then paste the selected prompt. The lead allocates work, resolves contracts, integrates results and reports evidence.

Codex supports explicit subagent requests independently of Ultra. Current releases enable delegation tools by default, and custom roles can choose their own model/effort. These facts were checked against official documentation through Context7. [Subagent documentation](https://learn.chatgpt.com/docs/agent-configuration/subagents).

## Project configuration

[.codex/config.toml](../.codex/config.toml) sets the lead/default workers to Luna/medium and caps spawned-agent threads at three, excluding the lead. Five [role files](../.codex/agents/earth_explorer.toml) define the team:

| Role | Model / effort | Responsibility |
|---|---|---|
| earth_explorer | GPT-6 Luna / medium | Read-only code and documentation mapping |
| earth_worker | GPT-6 Luna / medium | Bounded routine implementation |
| earth_specialist | GPT-6 Luna / medium | Geometry, concurrency and geospatial implementation |
| earth_reviewer | GPT-6 Luna / medium | Independent correctness review |
| earth_validator | GPT-6 Luna / medium | Assigned fixtures, tests and reproducible evidence |

These are deliberate task recommendations, not claims of measured model speedups. Use medium effort across the team. The host exposes GPT-6 Luna; the requested GPT-6.1 Luna identifier is unavailable.

Project configuration loads for trusted projects; explicit session settings can override defaults. Open a new session rooted at this package and verify actual settings. Opening only the nested source repository may not load the parent package configuration. No global config or permission policy was changed. [Configuration precedence](https://learn.chatgpt.com/docs/config-file/config-basic).

If custom roles are unsupported, generic subagents can use the same model/effort/instructions where the host supports overrides. If an override cannot be honored, record the actual inherited setting. A prompt cannot itself change the lead model.

## Shared preflight

Read AGENTS.md, the selected prompt, relevant docs and prior handoff. Confirm path, source revision and worktree status. Check available agent tools and model settings, and give every task explicit file ownership.

Spawn independent discovery tasks first. Freeze shared contracts before delegating implementation. Never delegate the same source file to simultaneous writers. Source bootstrap has one owner; subsequent Git integration has one owner.

Every prompt specifies task names and dependencies. Four tasks may run in multiple waves within the three-subagent limit. Release completed slots when the host supports it or queue work according to its thread controls. Workers do not recursively spawn.

Use Context7 for library/platform-dependent work as specified in AGENTS.md. Share fetched documentation findings between workers rather than performing identical searches.

## Verification and completion

Wait for actual worker results. Review the diff and evidence. Run the independent verifier/reviewer on a stable patch, resolve findings, and rerun affected tests. A production author is not its only reviewer.

Record task IDs, roles, requested/known actual settings, owned files, outputs and findings in the handoff. At least two distinct subagents must contribute. If delegation tools are unavailable, report that explicitly and leave the multi-agent gate pending; do useful preparatory work without inventing delegated execution.

Run heavy benchmarks one at a time. Give one agent ownership of a Blender scene or BeamNG connection. Others may research, analyze exported artifacts or review code concurrently.

## Keep the work focused

Use small fixtures, targeted reads and relevant tests. Batch independent discovery. Do not repeat passing checks without new changes or unresolved evidence. Keep each worker's context to its contract, assigned files and acceptance checks.

The phase stops at its acceptance gate and writes a concise handoff: revision, changes, tests/measurements, source versions, agent ledger, unresolved checks and next action. Do not mark game validation complete when only standalone tests ran.

The [execution plan](../config/execution-plan.json) mirrors the prompts. The [integration guide](08-integrations.md) identifies useful MCP tools and their current state.

