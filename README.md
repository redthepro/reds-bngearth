# BeamNG.Earth (Red's fork)

This is [redthepro/reds-bngearth](https://github.com/redthepro/reds-bngearth), a fork of
[TecnaGamer's BeamNG.Earth](https://github.com/TecnaGamer/BeamNG.Earth) focused on terrain loading,
driving quality, and reliability. **Version 1.8.3 includes upstream 1.6.2-beta plus the changes below.**
The original streamed world, map data integration, and BeamMP foundation come from TecnaGamer and the
upstream contributors.

A 1:1 scale, streamed Earth for [BeamNG.drive](https://www.beamng.com/): drive anywhere on the
planet on real elevation data and satellite imagery, with distance-based level of detail and the
real curvature of the Earth.

[Download this fork](https://github.com/redthepro/reds-bngearth/releases) ·
[Report a fork issue](https://github.com/redthepro/reds-bngearth/issues) ·
[Installation](#install-and-play) · [Social & voice](#social-and-voice-in-183) ·
[Changelog](CHANGELOG.md) · [Build status](https://github.com/redthepro/reds-bngearth/actions/workflows/release.yml)

## Social and voice in 1.8.3

This release adds **nearby positional voice, a server-wide channel, and private groups** to the fork's
terrain and driving improvements. Audio capture, Opus encoding, playback, and spatial mixing run in native
code in the companion. The downloadable Windows EXE and Linux AppImage include the voice client.

Join a BeamNG.Earth BeamMP server, then open **Pause → Mods → Earth Social & Voice**. The panel has a searchable
server player list, nearby/global/group channel cards, microphone/output selection, per-channel volume, player
mutes, and group invitations. The same panel is available inside Earth settings. It ships inside the main
EXE/AppImage’s mod and the matching BeamMP client ZIP; no extra client mod or Mumble app is needed.

Use the **Voice On / Voice Muted** slider: green enables voice for the session; red disables listening and microphone
capture. Device menus show the resolved system default and the actual devices in use. **Refresh devices** updates
the list without opening the microphone; selecting a device applies it immediately while voice is enabled.
Enable voice for the session to listen to nearby players and your private group. Use **Join global** to listen
or talk on the server-wide channel; global is off until you join it. Hold a key to talk:

| Channel | Default key | Who hears you |
| --- | --- | --- |
| Nearby | **Alt+V** | Players within 80 m, with distance fading and stereo direction |
| Global/server | **Alt+N** | Players connected to the server's voice service, after explicitly joining it |
| Group | **Alt+B** | Members of your private group |

Keys can be rebound in Controls. **Hold to test mic** runs a local meter test, with no encoded or transmitted audio.
Release, close the panel, switch focus, or wait 10 seconds to stop it. **Audio detected · microphone working**
confirms capture from the selected input; another player hearing you confirms the full connection. Outside this
explicit test or a granted push-to-talk press, microphone capture is idle. The driving HUD shows microphone use,
your transmitting channel, and players you are hearing.
Create a private group there, invite players currently connected to this server, accept invitations, or leave;
the owner can remove members. Invitations from disconnected players cannot be accepted.
Groups support 16 members and invitations expire after 30 seconds. Voice is opt-in again after leaving
multiplayer; releasing the key or losing game focus stops transmission.

**Map visibility:** open the world map’s **Players** menu and choose **Show me to other players**, or use the
matching switch in Social & Voice. It is off by default each session and works independently of voice. Hidden
players remain in the player list but have no map marker, are not used as automatic meeting points, and do not
supply the public server list’s location. This is map visibility, not anonymous play: BeamMP/server vehicle and
anchor synchronization still convey position for gameplay. Older servers do not expose the switch.

**Hosting:** the matching BeamMP server ZIP includes `VoiceService/` with the self-hosted Mumble service
and setup instructions plus **install_server.py**. From an unpacked server release run
`python install_server.py --server-dir /path/to/stopped/BeamMP` (`py -3` on Windows). The installer backs up replaced
Earth resources and preserves the operator’s config/AuthKey and other mods. Optional Linux voice installation:
`python3 install_server.py --server-dir /path/to/BeamMP --enable-voice --public-host YOUR_PUBLIC_IP_OR_DNS --start-voice`.
See the [server guide](beammp/README.md) for prerequisites, container registry mounts, verification and rollback.

| Service | Default host ports |
| --- | --- |
| BeamMP game | **30814 TCP + UDP** |
| Voice | **64738 TCP + UDP** |

The server host forwards these ports to its server machine. Voice authentication/control stays private
to the containers. See the [voice deployment guide](deploy/voice/README.md) for setup and limits.
The current companion uses Mumble's encrypted **TLS audio tunnel**; native UDP audio transport is future work.
The deployment is configured for 100 players, but has not been load tested with 100 players.

## What this fork changes

Compared with upstream 1.6.2-beta:

- **Terrain loading prioritizes driving.** Visual tiles along the car's predicted path are processed before
  other visible tiles, then the surrounding reserve. Follow-up collision blocks wait at most 150 ms for their
  aerial photo after heights are ready, at every driving speed; a slower photo arrives separately. Distant
  imagery can appear before shadow correction finishes, and optional texture corrections pause at high speed.
- **Streaming adapts to the running game.** Sustained slow frames and a growing import queue reduce optional
  visual work, then restore it gradually. The controller respects the foreground FPS cap and ignores background
  samples. Physics and road workers keep their separate queues; low-memory systems keep a smaller tile reserve.
  Material reloads are also charged against the frame's streaming budget.
- **Faster native elevation sampling.** Rust DEM resampling selects supported AVX2 or AVX-512 kernels at runtime,
  briefly comparing them when both are available, with a scalar fallback. This accelerates terrain height
  sampling while preserving the calculated heights; it does not replace BeamNG's renderer or vehicle physics.
- **Smoother roads and cleaner pavement.** Road grading preserves slopes and shared endpoints to reduce bumps
  between segments. Lane markings retain finer curves, mapped lane counts and widths take precedence over
  estimates, and tighter terrain blending reduces grass and dirt showing through pavement.
- **Better roadside placement and suburban buildings.** Roadside poles, signs, and their wires use terrain
  heights at their actual positions. Additional townhouse tags and low-rise, pitched-roof defaults improve
  eastern US suburbs, including Northern Virginia, while retaining mapped heights and roof types.
- **Working lights at night.** Nearby street and pole lamps receive actual scene lights that follow dusk and
  dawn, with distance fading and limits on how many lights are active or created at once.
- **A curated Washington, DC landmark pack.** The Washington Monument, Lincoln Memorial, and White House use
  attributed models with reduced distance LODs, prepared textures, and bounded loading. Large mapped buildings
  such as the Pentagon
  retain their walls beneath rooftop parts and keep their courtyards open, including in collision geometry.
  Custom visuals retain mapped building collision; this is a first DC detail pass, with placement still needing
  in-game comparison against the surrounding terrain. See [landmark credits](CREDITS.md#bundled-dc-landmark-models).
- **More resilient loading.** Invalid cached elevation blocks and map metadata are rebuilt, failed vegetation
  requests wait before retrying, and reused terrain material slots retain the correct source material.
  Network logs distinguish request-queue waits from HTTP time to help diagnose slow loading.
- **Authenticated compute sharing and fork-specific updates.** Peer work uses authenticated, replay-protected
  messages. BeamMP sharing credentials are scoped to a group's anchor, rotated when members leave, and cleared
  on disconnect; automatic BeamMP compute sharing remains disabled by default, as in upstream 1.6.2.
  Update checks and downloads use this fork's releases, with checksum verification and checks
  that staged updates belong to the same repository.
- **Automated builds for every main commit.** Windows and Linux regression tests gate compilation and packaged
  startup checks. Successful pushes publish a Windows EXE, Linux AppImage, matching BeamMP server ZIP, and
  `SHA256SUMS.txt`. Version bumps publish named releases; other commits get a tag containing the workflow run
  and commit.
- **Optional multiplayer voice.** Nearby positional, explicitly joined server-wide, and private-group push-to-talk channels run
  through a self-hosted Mumble service and native CPAL/Opus audio in the companion. In-game controls include
  devices, channel volumes, player mutes, group invitations and speaking indicators. Voice is disabled until
  enabled for the session; it uses a separate TLS connection. See [voice setup](deploy/voice/README.md).
- **In-game social controls and opt-in map markers.** A dedicated Mods panel brings together channels, mic testing,
  the player list, mutes and private-group invitations. Each player chooses whether to share their map marker.
  The server release bundles an installer and operator guide for game resources and the optional voice service.

The test suite includes an **offline 200 mph terrain-coverage scenario with delayed heightmap loading**.
It checks streaming coverage under controlled conditions; actual loading times and frame rates still depend on
the PC, location, cache, network, and BeamNG. Personal graphics settings applied to a test PC are not shipped as
a universal preset in this fork.

Upstream improvements remain included, such as the BeamMP server browser and launcher integration, Windows file
loading improvements, terrain shadow-stutter fixes, and bridge/tunnel loading repairs. See the
[changelog](CHANGELOG.md) for the inherited releases and this fork's additions.

Status: beta (see [VERSION](VERSION)); intended for BeamNG.drive 0.40.x. Upstream reports testing on 0.40.3
with Linux (Vulkan) and Steam Deck. The retained [roadmap](docs/ROADMAP.md) documents upstream plans.

**Community and credit:** the upstream [Discord server](https://discord.gg/tgf), #beamng-earth channel, is a place
to discuss the original project and find BeamMP.Earth servers. Please report issues with this fork in
[this repository](https://github.com/redthepro/reds-bngearth/issues). You can support the original developer through
[TecnaGamer's donation page](https://ko-fi.com/tecnagamer); that link supports upstream development.

## Features (working today)
**The world**
- The whole planet at 1:1 scale on real elevation: national lidar where it exists (Mapterhorn: much of
  Europe, the US and more), Copernicus 30 m elsewhere with buildings and small tree stands filtered out of it.
- Aerial and satellite imagery on the ground: open state and national orthophotos (Maryland's 15 cm photos,
  a dozen more US states, NAIP across the US, Switzerland, France, Spain, the Netherlands, Germany's states,
  Austria, Poland, Czechia, Japan, parts of Australia and Canada, and more), Sentinel-2 cloudless everywhere else; shadows and
  colour seams taken out of the photos, the season's colours graded in, cliffs drawn as rock.
- A real view distance out to the horizon, with the Earth's curvature.
- Both poles: Antarctica's ice sheet up to the South Pole (2,835 m) from the Copernicus DEM, the Arctic's sea ice
  at the North Pole, the polar day and night of the real sky, and a minimap compass that points to true north.
- Ground materials from land cover (grass, dirt, rock, sand, snow, mud, lawns, paved squares and parking
  lots) with 3D grass and bushes.
- Trees and forests from a canopy height model, regional species (conifers, broadleaf, palms, desert
  shrubs), autumn colours in season.
- Buildings from Overture / OpenStreetMap with regional styles (US siding and brick, Alpine chalets, Nordic
  wood, Mediterranean stucco, East Asian and Latin American looks), real heights, roofs, windows, shopfronts,
  porches, churches with steeples, glass towers, far-away buildings drawn as a skyline.
- Roads painted into the ground itself (no floating road surface), with lane markings, sidewalks and curbs, curb ramps, storm drains, cobblestone streets,
  traffic islands, roundabouts, rural motorways with shoulders and toll booths; the ground graded under them, with
  retaining walls where a road is cut into a hill (dry stone in the mountains and towns, concrete along major roads)
  and stone parapets along mountain roads' drops; street lights, utility poles with wires, traffic signals,
  stop signs and speed-limit signs (mph or km/h by country).
- Bridges drawn as their real type (beam, truss, arch, cable-stayed, suspension) with real heights and spans, piers
  that keep clear of the roads under them; tunnels through hills, with portals, lights and low arches where little
  is over them.
- Water: the sea with real depth (cars sink), lakes, and rivers kept beside the roads.
- Airports with graded, marked runways; racetracks (circuits, ovals and pit lanes, with their real banking);
  landmarks such as the pyramids of Giza.

**Time, weather and physics**
- **Real time**: the sun, moon and stars are where they really are for the place, the date and the clock
  (the real time zone); the clock runs in real time. Change the time in the pause menu and the mod hands it
  over to you (switch back to Real any time).
- **Real weather** at the player from Open-Meteo, updated as you travel: cloud cover and high cirrus, clouds
  drifting with the wind aloft, fog from the real visibility, ground wind with gusts that pushes the cars,
  rain and snow, the air temperature, and the sun dimming under overcast. It goes through the game's own
  weather system, so the Time & Weather menu shows the real values; a manual mode leaves it to you.
- **Seasons**: the photos' colours and the trees' leaves follow the time of year (autumn colours, bare
  trees in winter where the photos were taken leaf-off).
- **Real gravity**: the WGS84 gravity for your latitude and altitude plus the EGM2008 local anomalies, so
  gravity really is weaker on a mountain or at the equator.

**Driving and traffic**
- Physics terrain streams around and ahead of the car, while a floating origin keeps coordinates stable
  over long drives.
- Buildings, sidewalks, curbs and bridge decks are solid (lean collision meshes near the car keep the
  physics' collision rebuilds cheap).
- Fast teleports: right after a teleport, while you wait, the world server may borrow some of the game's
  CPU cores (at idle priority) to build the world faster (Linux; `--load-boost off` turns it off).
- **AI support**: the mod builds BeamNG's road graph (the AI's map) from the real roads around you, with
  lanes, one-way streets, speed limits (mapped, or the country's defaults), the country's driving side and
  road rules, and traffic signals at real junctions. The stock AI, traffic, police and the AI modes in the
  vehicle menu work on it.
- **Traffic**: stock BeamNG traffic and police can be switched on, and parked cars line the streets and fill
  parking lots (stock BeamNG parking).

**Multiplayer (BeamMP, in testing)**
- Play the real world together on a BeamMP server (every player runs the BeamNG.Earth app, which starts BeamMP with
  the game): BeamNG.Earth servers have their own **BeamNG.Earth** tab in BeamMP's server browser, players who join are
  sent to the others, cars show in the right place and with their crash damage in everyone's world, and other players
  show on the world map (see "Multiplayer" below).

**Map and navigation**
- A level select with famous places and a **Random location** button (it starts you on a road).
- World map (**M**): pan and zoom the globe, satellite or street map (EOX) with a roads & labels overlay,
  borders and place names (the **Layers** button), search for addresses and places as you type, teleport
  anywhere. Works with a controller, laid out like the stock map.
- GPS routes to any destination (OSRM on OpenStreetMap), with the stock route arrows on the road and the route
  on the game's minimap (bottom left), which shows the roads around you.
- **Autodrive**: the car drives itself along the GPS route (in lane, legal speed, minding traffic).
- Saved places and recent places.

**Interface and tools**
- A slim status bar (street, town, country, and under it the loading progress of the ground, roads & buildings, trees and
  distant land after a teleport), the Earth coordinates above the minimap, a connection banner when something is
  wrong and the data credits on screen, as BeamNG UI apps in a ".Earth" app layout (based on Freeroam's;
  move or resize them in the UI apps editor), and a one-time welcome page about the project.
- Settings in the pause menu, keybinds, in-game reports (notes and performance recordings to share), and a
  wireframe view (Ctrl+Alt+W) that shows how the ground, the distant terrain and the roads are built.
- A diagnostic log to send when something is slow: the world server's `logs/server.log` (in
  `%LOCALAPPDATA%\beamng-earth` on Windows, `~/.cache/beamng-earth` on Linux) records your hardware and settings, every
  hitch and frame-rate drop with where it happened and what was loading, and a summary every minute
  ([docs/QUICKSTART.md](docs/QUICKSTART.md#something-wrong-tell-us)).
- Easy setup: one download per system (Windows .exe, Linux AppImage) that installs the mod and starts the server
  and the game; from the source, `install.py` installs everything and `start.py` keeps the mod in sync.
- Runs on a Steam Deck (aim: 30 fps on the Handheld graphics preset).
- Uses your graphics card for some of the world server's work (colour matching of the photos, compressing the distant
  textures) when it can, so the server needs less of your CPU; without a usable one the CPU does it as before.
- Offline play from the download cache (with a size cap and "clear cache").
- Terrain loads progressively: distant tiles appear before shadow correction finishes, then receive updated
  textures. While driving, collision blocks wait at most 150 ms for their aerial photo after their heights are
  ready; a slower photo follows separately. First-anchor photos stay inline so they arrive with the new location.
- Updates: the mod tells you in game (and on its settings page, with a link) when a newer version is on GitHub;
  with the downloaded app (.exe / AppImage), **Update now** downloads it next to your copy, checks it against
  GitHub's checksum, and the next time you start BeamNG.Earth the new version starts (your old file and shortcut
  keep working).

## How it works
- A **companion server** (`server/`: Python, with its heavy work in a Rust module, `native/earthfast`) downloads
  elevation, imagery and map data, builds terrain, roads, buildings and the rest in a local flat frame around a
  moving origin, and streams them to the game over a local socket. The game's Lua cannot reach the internet, so the
  server is required.
- The **mod** (`mod/beamng_earth/`) loads the tiles as meshes, keeps an invisible physics terrain
  under the car, moves the world origin as you drive (floating origin), and blends level-of-detail
  changes so nothing pops.
- Design and measurements: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md),
  [docs/V1_4_SPEC.md](docs/V1_4_SPEC.md), research notes in [docs/research/](docs/research/).

## Install and play
**You need:** BeamNG.drive (tested with 0.40.x) on Steam, free disk space for the map download cache (it grows
as you visit places, up to 20 GB by default; settings page > "Download cache size" sets 5-50 GB), and an internet
connection (the map is downloaded while you play).

1. **Download** the file for your system from the [Releases](https://github.com/redthepro/reds-bngearth/releases)
   page:
   - **Windows:** `BeamNG.Earth-<version>.exe`. Windows may warn about an unknown app: **More info > Run anyway**.
   - **Linux:** `BeamNG.Earth-<version>-x86_64.AppImage`. Make it executable (right-click > Properties >
     "Allow executing", or `chmod +x`).
2. **Run it** (with Steam running). A console window opens (like BeamMP's launcher); it copies the mod into the
   game, starts the world server and starts BeamNG.drive. Closing the game stops the server.
   - **Linux, native game:** started with the grass patch (`tools/gcpatch`) already applied, no Steam launch
     options needed.
   - **Linux, Windows build under Proton:** the AppImage sees the Proton setting and starts that build through
     Steam (the server runs natively). The `.exe` also works inside the game's Proton prefix (for example with
     protontricks' launcher).
   - **Linux, Steam as a Flatpak (or snap):** the mod goes into the game's folder inside the Flatpak
     (`~/.var/app/com.valvesoftware.Steam/...`) and the game starts through Steam (without the grass patch).
   - The Windows build shows BeamNG's launch options dialog (renderer, safe mode) as usual; `--gfx dx11` /
     `--gfx vk` / `--gfx dx12` skip it with that renderer.
3. **Play:** in the game, go to **Freeroam** and pick the **BeamNG.Earth** level. A welcome page explains the
   basics the first time. Press **M** for the world map: search a place or an address, right-click to teleport,
   left-click to set a GPS route, and **Autodrive** to let the car drive it.

**From the source instead** (any system with [Python 3.10+](https://www.python.org/downloads/)): download this
project (the green **Code** button > **Download ZIP**) and unzip it. Install once (and after each update):
double-click `run_server.bat` (Windows) or run `python3 install.py` (Linux). Then start the server with
`run_server.bat` / `./run_server.sh`, keep its window open, and start the game yourself. Or run
`python3 launcher.py`, which also starts the game like the downloads do.

**Updating:** the mod tells you in game when a newer version is on GitHub. Download the new file and run it (from
the source: unzip it over the old folder and run the installer again).

**Uninstalling:** delete the `beamng_earth` folder in `<BeamNG user folder>/current/mods/unpacked/` and the
downloaded file (or this folder); the map cache is `.cache/beamng-earth` in your home folder (`~/.cache/beamng-earth` on Linux,
`C:\Users\<you>\.cache\beamng-earth` on Windows).

More detail (traffic, the status bar, reporting problems, troubleshooting): [docs/QUICKSTART.md](docs/QUICKSTART.md).

**Compute sharing (optional, for tinkerers):** another computer on your network can build part of the distant
landscape for you. On it, from a copy of the same version's source: `python3 server/peerwork.py serve` (port 47620,
`--workers N` processes; it downloads its own map data). On the computer you play on, set `BNGE_PEERS` to the
helpers before starting BeamNG.Earth, for example `BNGE_PEERS=192.168.1.20:47620,192.168.1.21:47620`.
Before starting either computer, set `BNGE_PEER_TOKEN` to the same random secret generated with
`python3 -c "import secrets; print(secrets.token_hex(32))"` (64 hexadecimal characters). For example, use
`$env:BNGE_PEER_TOKEN = '<secret>'` in PowerShell or `export BNGE_PEER_TOKEN='<secret>'` on Linux.
Helpers require this credential; old or incompatible helpers fall back to local generation. Messages are
authenticated and replay-protected, but not encrypted, so use a trusted network.

### Developer setup
`python3 install.py` also works from a checkout (mod copy, `.venv`, then `python3 start.py`); by hand:
1. Python 3.10+ with the packages in `server/requirements.txt`. The server needs its Rust module: the checkout
   carries it built for Linux x86-64 (`server/earthfast.abi3.so`) and Windows (`server/earthfast.pyd`); after
   changing `native/earthfast`, or on another system, build it with `python3 tools/build_native.py` (Rust 1.89+;
   `--windows` cross-builds the `.pyd` with cargo-zigbuild).
2. Copy (or sync) `mod/beamng_earth/` to
   `<BeamNG user folder>/current/mods/unpacked/beamng_earth/`.
3. Start the server: `python3 server/earthd.py`
4. Start BeamNG.drive and load the **BeamNG.Earth** level. The mod connects to the server and
   streams the world; the status bar at the top shows where you are and the loading, **M** opens the
   world map (search, teleport, GPS routes, Autodrive).

**Voice from source:** install CMake (`python -m pip install cmake`) and, on Linux, ALSA development
headers (`libasound2-dev` on Debian/Ubuntu). Run `python tools/build_native.py` to build the terrain and
native audio modules. Opus is bundled by the Rust build. The `--windows` cross-build option builds
the terrain module; build the audio module on Windows, as the Windows release job does.

Player downloads: `python3 tools/build_app.py` builds this system's AppImage / .exe (PyInstaller; see its
docstring; for the .exe run `tools/build_bootloader.py` first: a bootloader built from source, fewer antivirus
false positives). Every push to `main` runs the Windows/Linux regression suites, compiles the Rust kernels,
builds and smoke-tests both executables, and publishes a GitHub release with the matching BeamMP package and
`SHA256SUMS.txt`. A version bump publishes its named release, such as **`v1.8.3`**. Other commits use
`v<VERSION>-build.<workflow-run-number>.g<commit-sha>`. The selected version is embedded in the downloads.
Failed tests or builds prevent publication. Separate pushes keep their own builds; an older build finishing late
cannot replace the current main build as Latest. Preview/alpha/rc versions remain prereleases.
Manual Actions runs build downloadable artifacts by default; select **release** to publish them too.
The workflow uses GitHub's built-in token with release write access confined to the publishing job.
GitHub requires separate workflow-write authorization to backfill a commit whose workflow files differ
from current `main`; its built-in Actions token cannot provide that permission. Such historical builds
can be published from their tested artifacts using an authorized maintainer login. See
[GitHub's release permissions](https://docs.github.com/en/rest/releases/releases#create-a-release).
Release zip of the source: `python3 tools/package.py`.

Tests: `python3 tools/run_tests.py` and `python3 tools/run_lua_tests.py` (install LuaJIT, or `pip install lupa`
for its bundled LuaJIT on Windows). Run the two map suites with `node server/tests/test_mapmath.mjs` and
`node mod/tests/test_mapmath.mjs`. For the offline CI suite, set `BNGE_TEST_OFFLINE=1` and `BNGE_SKIP_DRIVE=1`;
live map-download and long network-drive tests remain available separately. The Windows/Linux regression jobs
must pass before release packages are built or published.

Hardware timing budgets are opt-in because shared CI runners have variable throughput. On an idle benchmark PC,
set `BNGE_PERF_TESTS=1` and run `python -m unittest discover -s server/tests -p test_colormatch.py -k test_per_tile_cost -v`.
The ordinary regression suite still checks numerical accuracy, image output, and Lua/Python behavior agreement.

`python server/tests/bench_loading.py` compares blocking and progressive terrain loading with controlled
one-second imagery delays. It runs offline and checks that the texture bytes match; its timings isolate
cosmetic waits rather than predicting an entire location's load time.

**Fork update channel:** `UPDATE_REPO` selects the GitHub repository used for update checks and downloads
(`redthepro/reds-bngearth` here). Set `BNGE_UPDATE_REPO=owner/repository` to override it, or `BNGE_UPDATE_REPO=off`
to disable updates. Missing or invalid configuration disables updates; it never falls back to upstream.
Release builds embed the CI repository, unless explicitly overridden. Updates staged for another repository
are not launched. A fork needs its own release assets before automatic updates can download anything.

## Multiplayer (BeamMP)

BeamNG.Earth runs on [BeamMP](https://beammp.com/) servers (in testing): drive the real world together.

**Joining a server**
1. Start the BeamNG.Earth app (the same version as the server's). It starts BeamMP with the game: the official BeamMP
   launcher, unchanged (BeamMP's code is AGPL-3.0, [github.com/BeamMP](https://github.com/BeamMP)). If BeamMP isn't
   installed, the app asks once: **Yes / No / Don't ask again**.
   - **Windows:** Yes downloads BeamMP's official launcher from BeamMP's GitHub releases (checked against the checksum
     BeamMP publishes; Windows Firewall then asks once whether it may use the network: allow it).
   - **Linux and Steam Deck:** BeamMP publishes no Linux download, so Yes builds its launcher on your computer from
     BeamMP's source, the newest version that passed BeamMP's own tests, in an Ubuntu container (podman or docker:
     SteamOS has podman; about a minute on a Steam Deck). A launcher you built yourself is used when found
     (`~/BeamMP-Launcher`, or set `BEAMMP_LAUNCHER`).
   - The setting **Start BeamMP with BeamNG.Earth** (Pause menu > Mods > BeamNG.Earth, Multiplayer) turns this off or
     back on; `--no-beammp` starts the game without BeamMP once.
2. In the game: main menu > More > BeamMP, log in (an account or a guest).
3. Open the **BeamNG.Earth** tab in BeamMP's sidebar: the servers running BeamNG.Earth, with a Join button. They are
   listed only there, for players who have BeamNG.Earth. A private server: Direct Connect with its address (the
   [Discord server](https://discord.gg/tgf) has some). Every player needs the BeamNG.Earth app running: each builds the
   world on their own computer.
   (Starting the BeamMP launcher yourself first still works: the app then starts just the world server.)

Leaving a server turns BeamNG.Earth back on (joining one switches your other mods off).

You are sent to where the others are. Cars show in the right place in everyone's world; other players show on the
world map (**M**) as green arrows; players far away (another country) are hidden until you meet. Crash damage: a crash
you watch keeps the damage your own game worked out; a car that crashed out of your sight takes on its owner's damage
(broken parts and dents) once it comes within about 0.6 miles.

**Sharing the work** (turned off for now: each player builds their own world)**:** players near each other share one
world origin and split the building of the distant landscape (each piece is built once and sent to the others; faster computers take more), while a player far from
everyone builds their own. For that the other players must reach your computer on a TCP port, 47621 unless the server
picks another (the server tells your app which): on the same network it just works; over the internet, forward that
port in your router, or you simply build everything yourself. A port of your own: set the environment variable
`BNGE_SHARE_PORT` before starting the app.

Use this fork's updated BeamMP server plugin and mod together. The plugin distributes session credentials only
to players sharing an anchor; credentials change when members leave, and leaving multiplayer closes the sharing
listener. Old plugins/clients cannot authorize shared work and generation stays local. Only join servers you
trust with the group's credentials. No manual `BNGE_PEER_TOKEN` is needed for BeamMP sharing.

**Hosting a server:** download `BeamNG.Earth-BeamMP-Server-<version>.zip` from the
[Releases](https://github.com/redthepro/reds-bngearth/releases) page and follow the guide inside
(`BeamNG.Earth-README.md`). In short: unzip it into a BeamMP server's folder, set
`Map = "/levels/beamng_earth/info.json"` in `ServerConfig.toml`, forward the server's port (TCP and UDP) for players
over the internet, and start it (its log shows `[BeamNG.Earth] plugin loaded`). Which list it is on:
`Private = false` with your AuthKey puts it on the BeamNG.Earth list (the in-game tab) and the plugin keeps it off
BeamMP's own public list, so players without BeamNG.Earth never see it; `Private = true`, or no AuthKey: on no list,
players use Direct Connect. The plugin lists the server once a minute with `curl` (Linux, and Windows 10 and later).
Update the server zip with every release: it carries the mod the server sends to players. The players' sharing port (47621) is
`G.SHARE_PORT` near the top of `Resources/Server/BeamNGEarth/main.lua`. The server needs no world data and
little CPU. From the source, `python3 tools/package.py --beammp` builds the same zip.

## Data sources and attribution
The repository contains only code; all map data is downloaded at runtime and cached locally.
- Imagery: state and national orthophoto services where they are open (Maryland, a dozen more US
  states, Switzerland, France, Spain, the Netherlands, Germany's states, Poland (GUGiK), Japan, parts of Australia and
  Canada, and more), USDA NAIP and USGS The National Map (public domain) in the US, and Sentinel-2
  cloudless by EOX IT Services GmbH everywhere (https://s2maps.eu, contains modified Copernicus Sentinel
  data; 2016 release CC BY 4.0, 2023 release CC BY-NC-SA 4.0, non-commercial).
- Elevation: Mapterhorn (see https://mapterhorn.com/attribution), AWS Terrain Tiles / Mapzen; the polar caps from
  85 deg on: Copernicus DEM GLO-30 (produced using Copernicus WorldDEM-30 (c) DLR e.V. 2010-2014 and (c) Airbus
  Defence and Space GmbH 2014-2018 provided under COPERNICUS by the European Union and ESA).
- Roads, buildings, water and places: Overture Maps / OpenStreetMap contributors (lane counts, widths and speed limits from OpenStreetMap via OSM US Layercake); trees from a
  canopy height model; airports from OurAirports.
Each source's terms apply to the downloaded data; the in-game window shows the attribution line.
The full list of data sources, services, libraries, tools and references, with licences and the
credit each one asks for, is in [CREDITS.md](CREDITS.md).

## License
Code: MIT, see [LICENSE](LICENSE). BeamNG.drive is a trademark of BeamNG GmbH; this is an unofficial
fan project and contains no BeamNG assets.
