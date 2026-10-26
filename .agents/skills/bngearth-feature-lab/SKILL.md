---
name: bngearth-feature-lab
description: Explore new BeamNG.Earth features or architectural expansions, compare approaches and build small measurable prototypes with a route to integration.
---

# Feature experiments and expansion

Use this skill for an actual new idea or redesign. Routine fixes belong in their domain skill. Keep a user's
brainstorm distinct from authorization to provision infrastructure, deploy a service, publish a post or enable a
feature for existing players. Respect ideas the user has paused or canceled; revive them only when requested.

## Find a useful experiment

Start with the player/operator outcome and the uncertainty worth resolving. Examples of useful questions:

- Can a regional cache reduce cold-start downloads without adding join latency or exposing session credentials?
- Can a different native kernel reduce generation time on a low-memory PC while preserving identical geometry?
- Can a richer social interaction fit the existing in-game panel and authoritative BeamMP player model?
- Can additional city landmarks fit the frame/memory budget with documented licenses and collision preserved?

Choose the smallest vertical slice that tests the question. Inspect existing companion, Lua, Rust and server extension
points before adding a new runtime. A new language/service needs a concrete benefit over the current boundary.

## Compare approaches

For significant alternatives, record a short decision note in a task-relevant location under `docs/`:

- User outcome and current bottleneck or missing capability.
- Options, including extending the existing implementation.
- A concrete hypothesis and success/failure measures: p95 stage latency, frame/import time, queue/memory bounds,
  terrain coverage, audio behavior or operator effort as appropriate.
- New data/protocol/state ownership, compatibility, failure behavior and resource cost.
- The smallest experiment, rollback/removal path and what would justify broader integration.

Keep the note proportional; an isolated small experiment does not need a large design document. Use current primary
documentation when an unfamiliar API or platform constraint matters. Label historical engine probes as historical.

## Prototype and integrate

- Keep uncertain behavior optional with a clear default, or isolate it on a branch/test harness. A prototype should
  exercise a real boundary without silently replacing the normal driving, voice or update path.
- For streaming experiments, preserve collision continuity, reserve coverage and low-spec throttling. For distributed
  work, define who owns identity/state and what happens on packet loss, partitions, stale data or incompatible clients.
- Preserve privacy and authorization boundaries. Peer meshes, Steam identity, cloud gateways or public endpoints
  need an explicit design and task scope; this skill does not pre-authorize deployment or spending.
- Use synthetic/offline fixtures first, then representative hardware or consenting multiplayer testing where needed.
  Separate warm/cold cache results and include failure cases. Compare the complete pipeline, not a microbenchmark alone.
- Promote a successful slice into its domain module, tests, packaging and documentation. Explain whether it is an
  experiment, implemented feature, local-game verification or multiplayer/load-tested behavior. Remove abandoned
  scaffolding and record the useful finding rather than leaving a permanent unfinished alternate system.

Use the streaming, geometry, social/voice, release or operations skill only when the experiment reaches that boundary.
New ideas are welcome; preserving a responsive, usable default experience is the condition for shipping them.
