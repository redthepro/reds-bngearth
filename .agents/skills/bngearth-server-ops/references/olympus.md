# Olympus operational notes

These notes describe the maintainer's existing target, not requirements for other deployments. Verify live state;
names, resources and service-manager behavior can change.

- SSH alias: `ssh olympus`, rootless Podman user `cpguimond`.
- Game service/container: `beammp-earth.service` / `beammp-earth`.
- Game data: `/home/cpguimond/beamng-earth-server/data`; stage packages beside it, not under `Resources`.
- Voice service/container: `beamng-earth-voice`; source under `~/beamng-earth-voice-src`, private data under
  `~/beamng-earth-voice-data`, shared registry under `~/beamng-earth-voice-registry`.
- Shared registry mounts: game `/server/voice:z`, voice `/game:z`. Keep it outside the game's parent private `:Z`
  bind mount, whose relabel on a restart previously prevented voice access. Private certificate/key data stays separate.
- Game settings: preserve AuthKey, 100 slots, one car and the configured port. BeamMP 3.9.3 needs `IP = "::"` here;
  forcing IPv4 previously produced `bad address cast` during authentication.
- Existing manual forwards are game 30814 TCP/UDP and voice 64738 TCP/UDP to Olympus. The UPnP renewal timer was disabled
  after the operator took ownership of these forwards. Do not enable it or replace mappings as a routine update.
- The old description of 23 GiB guest RAM is historical. The guest reported 8 CPUs and 7.2 GiB on 2026-10-26;
  read current guest and container limits before assigning resources. Other unrelated services run on this host.

## Service manager fallback

`systemctl --user` previously failed against the user-manager private socket, while the normal user bus remained
usable. Check current behavior first. If the same problem recurs, use the systemd manager over the user bus:

```sh
busctl --user call org.freedesktop.systemd1 /org/freedesktop/systemd1 \
  org.freedesktop.systemd1.Manager StopUnit ss beammp-earth.service replace
busctl --user call org.freedesktop.systemd1 /org/freedesktop/systemd1 \
  org.freedesktop.systemd1.Manager StartUnit ss beammp-earth.service replace
```

Await completion and confirm container state; the returned job path is not proof the server stopped. Shutdown can
take roughly 30–45 seconds. Do not restart the entire user manager, which owns unrelated workloads.

For logs, `sudo -n journalctl _SYSTEMD_USER_UNIT=beammp-earth.service -n 40 --no-pager -o cat` previously worked when
`journalctl --user` could not find journals. Limit output to relevant errors/public fields and do not echo credentials.

Use game established-connection counts plus the voice registry's player count to check idleness without printing
player records. Health checks should inspect listeners rather than creating fake player connections. Backups made
by `install_server.py` are under `data/backups/earth-<timestamp>/`; verify the chosen backup before rollback.
