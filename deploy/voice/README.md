# BeamNG.Earth voice

The companion embeds CPAL and Opus audio in `native/earthvoice`. Enable voice in
the in-game BeamNG.Earth settings. Capture runs only while a granted push-to-talk
key is held; losing focus, leaving the session, or missing the short control
lease stops transmission. It is disabled each new game session.

Open **Pause → Mods → Earth Social & Voice** (also available in Earth settings).
Default keys (rebind in Controls): Alt+V nearby (80 m), Alt+N global/server, Alt+B group.
Nearby and group voice become available after the session opt-in; global/server voice is a separate setting and
stays off until that player joins it. The settings page includes devices, three volume controls, player mutes,
speaking indicators, a live push-to-talk microphone level/verification state, group creation, owner
invitations/removal, accepting and leaving. The level is diagnostic only: it never exposes or retains raw PCM.
**Hold to test mic** uses native meter-only capture, without Opus encoding, transmission or playback. Release,
unmount, blur and a ten-second limit end the test; the worker/native short leases also fail closed. The driving
status HUD shows microphone activity, the active channel and names of players currently being heard.
Groups support 16 members; invitations expire after 30 seconds. Concurrent talker
limits are 4 server, 6 group, and 8 per proximity neighborhood.

## Hosting

For an unpacked server release, the preferred entry point is `install_server.py --server-dir /path/to/BeamMP
--enable-voice --public-host YOUR_PUBLIC_HOST --start-voice`. It installs resources and calls this directory’s
`install.py`. See `../BeamNG.Earth-README.md` in the release, or [the server guide](../../beammp/README.md) in Git.
The standalone voice installer is also available:

```sh
python3 VoiceService/install.py --game-dir /path/to/BeamMP --public-host YOUR_PUBLIC_HOST --dry-run
python3 VoiceService/install.py --game-dir /path/to/BeamMP --public-host YOUR_PUBLIC_HOST --start
```

Use a non-root Linux user with Python 3.11+, OpenSSL, Podman/Quadlet, subordinate UID/GID ranges and a user systemd
session. No pip packages are needed on the host. The generated unit maps the host user to the image’s UID/GID,
so the host account need not be UID 1000. `--registry-dir` selects a separate shared mount for containerized games;
`--data-dir` selects persistent private voice data. `--port` selects a published port (default 64738). The installer
prints the forwarding and mount requirements and leaves router, firewall and game config under operator control.
It preserves existing certificates/secrets; back these up privately before migrating a host.

Manual setup remains available below; the checked-in Quadlet is an Olympus example, not a portable account/path
template. Use the installer-generated unit on other hosts.

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
   localhost/beamng-earth-voice:1.8.2 <directory>`.
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
