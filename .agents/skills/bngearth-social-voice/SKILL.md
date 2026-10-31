---
name: bngearth-social-voice
description: Implement or debug BeamNG.Earth social and voice UI, audio devices and mic indicators, proximity/global/group channels, connected-player groups or opt-in map visibility.
---

# Social, voice and player visibility

Trace the whole control path before replacing the audio backend:

`EarthSocial.vue → socialBridge.mjs → core.lua/voice.lua → local companion voice worker → earthvoice/Mumble`.

Server identity/group policy lives in `beammp/Server/BeamNGEarth/lib/voice.lua`; public player/map state also uses
the plugin's `main.lua` and Lua `mpsession.lua`. Native audio lives in `native/earthvoice`; the Python worker and wire
transport live in `server/voice.py`, `voice_client.py`, `voice_wire.py`. Hosted policy is under `deploy/voice/`.

## Controls and contracts

- `bngApi.engineLua(script, callback)` interpolates an expression into a Lua callback argument. Use the existing
  `socialBridge.mjs` expression helper; raw `local ...; return ...` chunks caused the entire panel to stay at Voice off.
  Test the generated expression inside the actual Lua wrapper, not just Vue compilation/SSR.
- Keep Lua session options authoritative. Send a changed field and preserve other choices; stale polling must not
  reset enabled state, global membership or device selection. Handle Lua's empty tables as well as JS arrays.
- Keep the master switch green for enabled and red for muted, with text/accessible switch state. Global requires its
  own Join action. Leaving multiplayer resets opt-in; an anchor gap during menu closure does not.
- Enumerate devices while muted without constructing streams. Show resolved defaults and active native device names.
  Reopen the engine when a selection changes, and expose errors rather than silently selecting an unrelated device.
- The meter test is explicit, local-only, bounded and stops on release/focus loss/unmount. Never activate a real
  microphone as an automated smoke test. Mock engines/clients and synthetic Opus frames cover routing without capture.
- Preserve worker ownership and bounded queues/leases. The present client transport is Mumble TLS audio, not native
  UDP audio. Keep certificate pinning and avoid exception text or credential-bearing state in logs/UI.
- Invite only current server players using authoritative IDs. Validate both sender and recipient at the server;
  reject expired/stale invitations and enforce ownership/member limits. Names are display text, never identity keys.
- Map visibility defaults off and is independent of voice. Hidden map markers do not remove vehicle/anchor sync.

## Validate and describe evidence

- Python: `test_voice`, `test_voice_wire`, `test_social_bridge`, `test_protocol_agreement`.
- Lua: `test_voice.lua`, `test_voice_server.lua`, `test_mpsession.lua` and affected core tests.
- UI: `node mod/tests/test_social.mjs`, then `node tools/check_social_ui.mjs` after installing its pinned dependencies
  from `.github/workflows/tests.yml` into `build/social-ui-check`.
- Policy: `python deploy/voice/test_policy.py`; native: `cargo test --locked --manifest-path native/earthvoice/Cargo.toml`.

Test mute/enable, device changes, global join/leave, stale roster entries, group permissions, focus loss, reconnect and
session reset when those paths change. A user-confirmed local mic meter verifies capture; another player's reception
verifies the network/channel path. Do not describe one as proof of the other.

Ship matching client mod/native companion and server resources when required. Updating the BeamMP ZIP alone does not
replace an already-running companion's native audio library; explain the close/relaunch/rejoin step.
