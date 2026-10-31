# BeamNG.Earth on a BeamMP server

This zip turns a BeamMP server into a BeamNG.Earth server: the real world, streamed around every player. Players join
with the normal BeamMP launcher.

Optional nearby, server-wide and private-group voice uses the matching companion
and a self-hosted Mumble service. Setup files are included in `VoiceService/`;
see `VoiceService/README.md`. Voice requires **64738 TCP + UDP** in addition to
the game's port, and remains disabled until each player enables it in settings.
Nearby and group voice are then available; server-wide voice additionally requires
that player to explicitly join the global channel.

## Installer (1.8.2 and newer)

1. Install the official [BeamMP server](https://docs.beammp.com/server/create-a-server/), run it once to create
   `ServerConfig.toml`, then stop it. Set your own AuthKey, map `/levels/beamng_earth/info.json`, name and player
   limit in that file. The Earth installer preserves this configuration.
2. Download the matching `BeamNG.Earth-BeamMP-Server-VERSION.zip` and extract it into a **separate staging folder**.
   Keep `install_server.py`, `VERSION`, `Resources/` and `VoiceService/` together. Python 3.10+ is required for game
   resources; Linux voice setup needs Python 3.11+, OpenSSL, rootless Podman with Quadlet, a user systemd session,
   and configured subordinate user/group IDs. The voice image supplies its own runtime dependencies.
3. From that staging folder, preview the update, then install it:

   ```sh
   python3 install_server.py --server-dir /path/to/BeamMP --dry-run
   python3 install_server.py --server-dir /path/to/BeamMP
   ```

   Windows PowerShell: `py -3 .\install_server.py --server-dir C:\BeamMP`. Voice hosting can run separately on a
   Linux host; game resource installation works on Windows and Linux. The installer refuses an update if the
   configured game port accepts a local connection. Stop the process/container yourself first; this check cannot
   detect a server bound exclusively to another address or a container with a different published port.
4. To install and start voice on a **bare-host Linux BeamMP server**, use the same Linux user as the game:

   ```sh
   python3 install_server.py --server-dir /path/to/BeamMP \
     --enable-voice --public-host voice.example.org --start-voice
   ```

   Replace the hostname with a working public DNS record or IP; the script does not create DNS/forwarding rules.
   It builds the pinned voice container, creates a stable TLS certificate and private registry, writes a user
   Quadlet, reloads the user manager, and starts the voice service. It preserves existing TLS/authenticator secrets.
   Leave off `--start-voice` to install without starting it. Add `--dry-run` to check prerequisites and paths only.
   For boot startup, the host administrator must enable lingering for this user (`loginctl enable-linger USER`).
5. For **containerized BeamMP**, use a dedicated shared registry outside its private data volume:

   ```sh
   python3 install_server.py --server-dir /path/to/BeamMP/data \
     --enable-voice --public-host voice.example.org \
     --registry-dir /home/USER/earth-voice-registry --start-voice
   ```

   Add the game container mount `/home/USER/earth-voice-registry:/server/voice:z`. The generated voice service mounts
   the same directory at `/game:z`. Share the label with `:z` on both; keep this directory outside any parent
   `:Z` volume so a game restart cannot relabel it. Run both services under the same host user. The installer prints
   the required mount, but does not modify an existing game container. For Windows game hosting, expose the private
   registry to a trusted Linux voice host using an operator-managed private filesystem; do not expose it publicly.
6. Forward the game port (**30814 TCP/UDP** by default) and voice port (**64738 TCP/UDP**) to the relevant host(s),
   and allow them in the host firewall. Never forward private controller ports 6502/6503.
7. Start BeamMP. Its log should include `plugin loaded` and, when voice is configured, `optional voice broker
   loaded`. Check voice with `podman ps --filter name=beamng-earth-voice` and `podman logs beamng-earth-voice`;
   look for `Voice authenticator ready`. The user service is `beamng-earth-voice.service`.
8. Two players run the matching companion EXE/AppImage, reconnect, and open **Pause → Mods → Earth Social & Voice**.
   Enable voice, hold the mic-test button to verify local capture, then test Alt+V within 80 m. Each player must
   explicitly **Join global** for Alt+N; create/invite/accept a private group for Alt+B. Test map visibility with one
   player opening **Map → Players → Show me to other players**. Turning it off removes that marker.

Updates back up replaced Earth files under `SERVER/backups/earth-TIMESTAMP/`, outside downloaded resources. A copy
failure restores files already replaced. For manual rollback, stop BeamMP and restore those resource files; leave
`ServerConfig.toml`, other mods and the private voice registry alone. Back up the voice data directory separately
(certificate, key, database and authenticator secret). Treat that backup as private. An image/configuration failure
can be inspected before restarting the game; the installer does not silently restart it or change the AuthKey.

The main EXE/AppImage already installs the social UI, map controls and voice client. There is no extra player mod
to distribute. Voice, global membership, and map-marker sharing are off by default each new multiplayer session.
Map visibility does not disable vehicle/anchor networking and is not a way to hide a position from the server.

## Manual installation

Install the matching app, mod and server plugin from this fork. Compute sharing now authenticates the active
anchor group using random session credentials relayed by the plugin. Old plugins/clients fall back to local
generation. Credentials are not logged or included in the public position updates; only group members receive
them. Leaving multiplayer revokes the credentials and closes the player's sharing listener. The server and
members of the group must be trusted; peer traffic is authenticated, not encrypted.

## Quick server setup

1. **Get the BeamMP server** (the latest release from https://github.com/BeamMP/BeamMP-Server/releases; Windows:
   `BeamMP-Server.exe`, Linux: the build for your distribution). Put it in its own folder and run it once: it writes
   `ServerConfig.toml` and a `Resources` folder, then stops or waits. Close it.
2. **Unzip this file into that folder.** Its `Resources` folder merges with the server's:
   - `Resources/Client/beamng_earth.zip`: the BeamNG.Earth level and mod (BeamMP sends it to every player who joins);
   - `Resources/Server/BeamNGEarth/main.lua`: the server plugin (it tells the players where everyone is).
3. **Edit `ServerConfig.toml`:**

   ```toml
   Map = "/levels/beamng_earth/info.json"
   Port = 30814          # any port you like; players connect to it
   Name = "My BeamNG.Earth server"
   MaxCars = 3
   Private = false       # false (with your AuthKey): on the BeamNG.Earth server list; true: direct connect only
   AuthKey = "..."       # your own key from https://keymaster.beammp.com (the server won't start with it empty)
   ```

   Never share your AuthKey (the plugin only checks that one is set; it never sends it anywhere).

   **Which list your server is on:**
   - `Private = false` with your AuthKey: on the **BeamNG.Earth** server list, the "BeamNG.Earth" tab in BeamMP's
     server browser, which only players with BeamNG.Earth see. The plugin keeps it off BeamMP's own public list while
     the server runs (it switches BeamMP's Private setting on; your file isn't changed), so players without
     BeamNG.Earth never see it.
   - `Private = true`, or no AuthKey: on no list. Players join with Direct Connect and your address.
   - To keep a public server off the BeamNG.Earth list too, set `G.LISTED = false` near the top of
     `Resources/Server/BeamNGEarth/main.lua`.

   The server lists itself once a minute with `curl` (built into Linux and Windows 10 and later).

   **Time and weather** (near the top of `Resources/Server/BeamNGEarth/main.lua`, then restart the server):
   - `G.TIME = 'real'` (each player's real local time where they are) or a fixed local time such as `'20:30'`, with
     `G.TIME_SPEED` 0 (it stays), 1 (it runs at real speed) or more (60: an hour of the day every real minute). Every
     player sees the same time.
   - `G.WEATHER = 'real'` (the live weather where each player is) or `'clear'`, `'cloudy'`, `'rain'`, `'storm'`,
     `'snow'`, `'fog'` for everyone.
   - A player who set Sky or Weather to Manual in BeamNG.Earth's settings keeps their own.
4. **Open the port** if players come over the internet: forward the server's port, **TCP and UDP**, in your router to
   the server's computer (and allow it in its firewall). Players on the same network need nothing.
5. **Start the server.** Its log should show `[BeamNG.Earth] plugin loaded`.
6. **Players connect** from BeamMP's server browser (main menu, More, BeamMP): the **BeamNG.Earth** tab for listed
   servers, or Direct Connect with your address and port.

To update, unzip the new version's server zip over the old one and restart the server. The server needs no world
data and little CPU: the world is built on each player's computer.

## Players

- **Every player needs the BeamNG.Earth app** (https://github.com/redthepro/reds-bngearth/releases) running, the same
  version as the server's. Start the BeamNG.Earth app and the BeamMP launcher in either order (the app starts the game,
  or just its world server if the game is already running), then join. Without the app, a window asks to install it:
  the world can't load without it.
- A player who joins is sent to where the others are, and each player's car shows in the right place in everyone's
  world, with its dents. Other players show on the world map (M) as arrows.
- (Turned off for now: each player builds their own world, and the setting below does nothing yet.) Players near
  each other share one world origin and split the work of building the world: each piece of the distant
  landscape is built once and sent to the others. For that, the other players must reach each player's computer on
  **TCP port 47621** (on one network it just works; over the internet, each player forwards it, or else simply builds
  everything themselves). To use another port on your server, edit the line `G.SHARE_PORT = 47621` near the top of
  `Resources/Server/BeamNGEarth/main.lua` and restart the server: every player who joins gets it. A player can still
  pick their own with the environment variable `BNGE_SHARE_PORT`.
- Players far apart (another country) each build their own world and don't see each other's cars until they meet.
