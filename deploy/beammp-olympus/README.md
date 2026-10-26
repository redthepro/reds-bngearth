# BeamMP.Earth on Olympus

This is a standard BeamMP server with the BeamNG.Earth client mod and server plugin. Each player's companion app generates terrain locally. Olympus exposes only **30814 TCP and UDP**, with no companion or compute-sharing endpoint.

The server name is exactly `github.com/reds-bngearth - 1.7.0 Development Test`. It is configured for 100 player slots and one car per player, with limits of four CPUs, 4 GiB RAM and 512 processes on the eight-CPU, 23 GiB host. These are configured slots; 100-player performance has not been load tested. It runs as the rootless Podman user with a read-only image and persistent data under `~/beamng-earth-server/data`.

The image uses the official [BeamMP 3.9.3 stable release](https://github.com/BeamMP/BeamMP-Server/releases/tag/v3.9.3), the Debian 12 x86_64 executable, and its GitHub-published SHA-256 `8d24d463542be2e1d0e38c5eb58a1c168aa8c1a1309170a8bbdcdbdc27e2d6f1`. The base image is pinned by digest. The build verifies the executable hash and runs its version command. The Earth package initially comes from tested commit `605b94514d1a5fa230531bfe56641b4af81e7d33`; deployment provenance records its checksum.

Enter the separate BeamMP AuthKey from [Keymaster](https://keymaster.beammp.com) directly on Olympus:

```sh
ssh -t olympus ~/beamng-earth-server/set_auth_key.py
```

The prompt is hidden. The key is never a command argument or shell-history entry, and only the remote configuration stores it, with permissions `0600`. The service's startup condition leaves it inactive until a key is present. The helper saves the key and starts the service. Never copy the populated configuration into this repository.

Useful commands:

```sh
ssh olympus systemctl --user status beammp-earth.service
ssh olympus journalctl --user -u beammp-earth.service -n 40 --no-pager
ssh olympus podman healthcheck run beammp-earth
ssh olympus systemctl --user restart beammp-earth.service
```

The Quadlet is installed at `~/.config/containers/systemd/beammp-earth.container`; the generated service follows the user's default target and existing user lingering keeps it available after logout/reboot. Systemd restarts exits, and three failed health checks trigger a restart. Config, resources and the server's own logs remain in the data directory. Updating resources requires a deliberate stop, package verification/extraction, then restart; do not mix client/plugin versions while players are connected.

Internet forwarding maps external **30814 TCP + UDP** to **192.168.86.100:30814**. Both mappings were created through the router's UPnP IGD and read back as enabled. The host's active `lan` firewalld zone allows both ports in runtime and permanent configuration. No other port was opened for this deployment. The Earth plugin lists a public configured server on the Earth-specific list and prevents incompatible ordinary BeamMP clients seeing it on the generic list.

Connect from the home LAN to **192.168.86.100:30814**. Outside players can use **108.28.190.39:30814**, the public IPv4 observed on 2026-10-25. External TCP connections succeeded from Los Angeles and Nuremberg ([test report](https://check-host.net/check-report/537441f5ke78)); the same LAN's connection through the public IP was refused, consistent with missing NAT loopback. UDP forwarding, host permissions and the server listener were verified; a full outside BeamMP game session has not been tested. The public IP can change.

`olympus-vii.org` currently returns NXDOMAIN for public A and AAAA lookups, so it is not a working connection address. Once its registration/delegation and DNS zone are ready, an A record must point the chosen hostname directly to the current public IPv4. DNS was not modified. No working global IPv6 address was found on Olympus.

The router limits UPnP mappings to seven days and acknowledges renewal without extending an existing lease. The installed `beammp-earth-upnp.timer` checks every 30 minutes and after user-manager startup; `upnp-renew.py` communicates directly with the discovered router's IGD HTTP endpoint, using only Python's standard library. It checks both mappings for ownership before writing. If the router still will not extend a lease with under two hours remaining, it briefly deletes and recreates only that exact owned mapping. It refuses to replace another application/client's entry. The temporary gateway-only SSDP discovery firewall rule was removed; no permanent discovery port or `/tmp` tool is required.

Recreation uses at most three attempts with ownership reads before every write and readback afterward; a lost success response does not trigger a duplicate write. Verification includes the application description as well as the destination address and port. The oneshot service retries failures after 15 seconds, limited to three starts per 30 minutes, so a transient router failure after deletion does not wait for the next regular timer.

UPnP maintenance commands:

```sh
ssh olympus python3 ~/beamng-earth-server/upnp-renew.py --check
ssh olympus systemctl --user list-timers beammp-earth-upnp.timer --all
ssh olympus systemctl --user status beammp-earth-upnp.service
ssh olympus systemctl --user start beammp-earth-upnp.service
```

The helper and unit sources are included here. Bounded recovery handles lost router replies and rechecks ownership before restoring a mapping; the service also retries failures after 15 seconds, at most three starts in 30 minutes. Installed units are under `~/.config/systemd/user/`; existing user lingering keeps the timer active after logout. If the router changes or UPnP is disabled, use manual persistent forwarding rules for external **30814/TCP → 192.168.86.100:30814/TCP** and **30814/UDP → 192.168.86.100:30814/UDP**, reserve Olympus's LAN IP, and disable the timer before taking ownership of those entries manually. No host firewall changes beyond the two existing game-port allowances are necessary.

The helper's conflict, expiry and SOAP compatibility checks run with:

```sh
python3 deploy/beammp-olympus/test_upnp_renew.py
```

References: [official setup](https://docs.beammp.com/server/create-a-server/), [server settings](https://docs.beammp.com/server/server-maintenance/), [Podman Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html).
