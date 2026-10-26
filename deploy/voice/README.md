# BeamNG.Earth voice

The companion embeds CPAL and Opus audio in `native/earthvoice`. Enable voice in
the in-game BeamNG.Earth settings. Capture runs only while a granted push-to-talk
key is held; losing focus, leaving the session, or missing the short control
lease stops transmission. It is disabled each new game session.

Default keys (rebind in Controls): Alt+V nearby (80 m), Alt+N server, Alt+B group.
The settings page includes devices, three volume controls, player mutes, speaking
indicators, group creation, owner invitations/removal, accepting and leaving.
Groups support 16 members; invitations expire after 30 seconds. Concurrent talker
limits are 4 server, 6 group, and 8 per proximity neighborhood.

## Hosting

The optional BeamMP plugin activates when `voice/endpoint.json` exists in the
server working directory. This directory **must remain outside Resources**.
The controller and BeamMP share it privately. The controller generates player
credentials and revokes them when the trusted game registry expires or a player
leaves. No Steam login or extra account is required.

`Containerfile` pins the official Mumble 1.5.939 image by digest and adds its
private Ice controller. Both processes share a network namespace; Ice ports
6502/6503 bind loopback and are never published. The supervisor stops the voice
listener if the controller exits or its heartbeat stalls. Rootless Podman limits
the service to 2 CPUs, 2 GiB RAM and 128 processes; 300 voice connections cover
100 players with three channels each. This is a configured capacity, not a
100-player load-test result.

1. Copy this directory to the host and build with `podman build -t
   localhost/beamng-earth-voice:1.7.0 <directory>`.
2. Run `python3 initialize.py --data ~/beamng-earth-voice-data --game
   ~/beamng-earth-voice-registry --host YOUR_PUBLIC_IP_OR_DNS`. It generates
   a stable certificate and private secrets without printing credentials.
3. Install `beamng-earth-voice.container` in the user's Quadlet directory, reload
   the user manager and start its generated service. Adjust host volume paths
   for a different BeamMP installation. Mount the separate registry directory at
   `/server/voice` in BeamMP and `/game` in the voice service using shared SELinux
   labeling (`:z`). Keep it outside the privately labeled game data volume so a
   game restart cannot relabel and interrupt the running voice service. Do not
   copy populated config or credentials into Git.
4. Install the matching BeamMP client ZIP/plugin and companion release. Restart
   BeamMP to load the plugin.
5. Open/forward **64738 TCP and UDP** to the voice host. The game continues using
   **30814 TCP and UDP**. Never expose Ice 6502 or 6503.

On Olympus: voice and game ports forward to **192.168.86.100**. The public IP is
configured in the endpoint file; change it there if the ISP address changes.

## Transport and isolation

This implementation uses Mumble's standard encrypted TLS audio tunnel on its own
connections, independently of terrain streaming and game networking. It does not
yet use native UDP voice transport; packet loss on TCP may increase voice latency.
The UDP port also supports standard Mumble server traffic and future client work.
Clients pin the exact server certificate **before sending credentials**.

Each player has separate nearby and server connections, plus a group connection
while grouped. Private group membership and passive listening are enforced by
server ACLs. Each nearby connection has its own speaking room; explicit whisper
recipients keep nearby audio from broadcasting to the whole server. Distance and
stereo direction use ECEF coordinates, preserving spatial sound through rebases.
The normal companion selects recipients from the trusted server's nearby list;
proximity distance is not an adversarial security boundary against a modified
client. Private group access is enforced by the server.

## Validation

`python deploy/voice/test_policy.py`, native Cargo tests, Python voice and protocol
tests, and Lua client/server tests run without microphones. The explicit
`integration.py --isolated-registry` test sends synthetic Opus through a real
Mumble server and checks nearby, global, group and rejected outsider access.
It temporarily owns the voice registry: **run only before enabling the live
BeamMP plugin**, never against active players' voice sessions.

Vendored Ice schema and its license are from the Mumble v1.5.939 release.
