---
name: bngearth-server-ops
description: Install, update or diagnose the BeamNG.Earth BeamMP and Mumble services, including rootless Podman deployment and authorized Olympus operations.
---

# BeamMP and voice operations

Read `beammp/README.md`, `beammp/install_server.py`, `deploy/voice/README.md` and the target's actual configuration
before making a deployment concrete. For the maintainer's Olympus host, read [references/olympus.md](references/olympus.md).
This skill supplies procedure, not permission to deploy or interrupt players.

## Install or update

1. Establish the requested version/source commit and whether the task is a read-only diagnosis, local staging or an
   authorized live change. Inspect players, current health, resources and rollback location before a restart.
2. Stage a verified server ZIP. Check its version, digest and safe extraction paths. Prefer the packaged installer,
   which validates resource paths, copies with verification and backs up replaced files outside client downloads.
3. Keep game configuration/AuthKey and unrelated mods. Private voice registry, credentials, certificates, database and
   control endpoints belong outside `Resources`; neither the client ZIP nor an archive backup there may expose them.
4. Stop only the intended game service for resource installation and confirm it is stopped. Use a `finally`/recovery
   path to restore service availability if installation fails. Avoid interrupting active players without coordination.
5. Restart and verify TCP/UDP listeners, container health, startup errors and installed resource hashes. Distinguish
   an open port from a successful game join, and microphone capture from audio heard by another player.
6. Tell players whether they need a new companion and rejoin. The server-supplied mod cannot upgrade native code in
   an already-running EXE/AppImage. Report version, source, backup location and any remaining test limitations.

## Boundaries

- Default game port: 30814 TCP/UDP. Voice host port: 64738 TCP/UDP. Mumble control/Ice endpoints remain private.
  Use actual configured ports when diagnosing; do not open additional firewall/router rules without task scope.
- Keep operator-owned manual forwards intact. UPnP should verify ownership and handle lost replies; it is optional.
- BeamMP relays sessions; clients build terrain and simulate vehicles. A low CPU figure with few players is expected
  and is not evidence that the host should take over terrain generation. Benchmark changes before sizing for 100 users.
- Inspect effective container/guest CPU and memory. A 100-slot configuration is not a 100-player load-test result.
- Never print `ServerConfig.toml`, container environments or credential registries wholesale. Report selected public
  fields, counts and hashes. Use headless synthetic voice tests; real microphone capture requires user action.

Use `test_server_installer`, `deploy/voice/test_policy.py`, applicable Lua plugin tests and isolated installer dry-runs
for changed deployment tooling. Do not run an installer test against a live production registry or restart all user
services to fix one unit.
