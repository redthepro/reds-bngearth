#!/usr/bin/env python3
"""Install a private Earth voice service using Linux rootless Podman and Quadlet.

The game must mount the registry at /server/voice when it runs in a container.
No router, firewall, game configuration, or authentication key is changed.
"""
import argparse
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


def unit_text(data, registry, image, port):
    # systemd specifiers and line breaks must not become directives in paths.
    def path(value):
        value = str(value)
        if any(c in value for c in '\n\r\0:'):
            raise ValueError('Container volume paths cannot contain colons or newlines.')
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
    return f'''[Unit]
Description=BeamNG.Earth voice and private authenticator
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300
StartLimitBurst=5

[Container]
Image={image}
Pull=never
ContainerName=beamng-earth-voice
UserNS=keep-id:uid=1000,gid=1000
User=1000
Group=1000
Volume={path(data)}:/data:z
Volume={path(registry)}:/game:z
PublishPort=0.0.0.0:{port}:64738/tcp
PublishPort=0.0.0.0:{port}:64738/udp
ReadOnly=true
ReadOnlyTmpfs=true
NoNewPrivileges=true
DropCapability=all
PidsLimit=128
PodmanArgs=--cpus=2 --memory=2g --memory-swap=2g
StopTimeout=15
LogDriver=journald

[Service]
Restart=always
RestartSec=5
UMask=0077
TimeoutStartSec=180

[Install]
WantedBy=default.target
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--game-dir', type=Path, required=True)
    parser.add_argument('--public-host', required=True)
    parser.add_argument('--registry-dir', type=Path)
    parser.add_argument('--data-dir', type=Path, default=Path.home() / 'beamng-earth-voice-data')
    parser.add_argument('--port', type=int, default=64738)
    parser.add_argument('--start', action='store_true')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    if sys.platform != 'linux' or os.getuid() == 0:
        parser.error('Run as the Linux user who hosts BeamMP; use rootless Podman, not root/sudo.')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9.:-]{0,252}', args.public_host):
        parser.error('Provide a public IP or DNS name without a URL scheme or path.')
    if not 1024 <= args.port <= 65535: parser.error('Choose a host port between 1024 and 65535.')
    for command in ('podman', 'openssl', 'busctl'):
        if not shutil.which(command): parser.error(f'Install {command} first; see README.md prerequisites.')
    source = Path(__file__).resolve().parent
    version_file = source.parent / 'VERSION'
    version = version_file.read_text().strip() if version_file.exists() else 'local'
    if not re.fullmatch(r'[0-9A-Za-z_.-]+', version): parser.error('Invalid package version.')
    image = 'localhost/beamng-earth-voice:' + version
    data = args.data_dir.expanduser().resolve()
    game = args.game_dir.expanduser().resolve()
    registry = (args.registry_dir or game / 'voice').expanduser().resolve()
    if not (game / 'ServerConfig.toml').is_file(): parser.error('Choose the existing BeamMP server directory with ServerConfig.toml.')
    if registry == game or registry == data or registry.is_relative_to(game / 'Resources') or data.is_relative_to(game / 'Resources'):
        parser.error('Keep voice data and registry separate and outside the game Resources directory.')
    text = unit_text(data, registry, image, args.port)
    unit = Path.home() / '.config' / 'containers' / 'systemd' / 'beamng-earth-voice.container'
    print(f'Voice image: {image}\nPrivate data: {data}\nShared registry: {registry}\nQuadlet: {unit}')
    print(f'Forward {args.port}/TCP and UDP to this host. Keep Ice 6502/6503 private.')
    if registry != game / 'voice':
        print(f'Containerized BeamMP: mount {registry} at /server/voice with shared SELinux labeling (:z).')
    if args.dry_run: return
    os.umask(0o077)
    subprocess.run(['podman', 'build', '-t', image, str(source)], check=True)
    subprocess.run([sys.executable, str(source / 'initialize.py'), '--data', str(data), '--game', str(registry),
                    '--host', args.public_host, '--port', str(args.port)], check=True)
    unit.parent.mkdir(parents=True, exist_ok=True)
    if unit.exists(): shutil.copy2(unit, unit.with_suffix('.container.previous'))
    staged = unit.with_suffix('.container.new')
    staged.write_text(text, encoding='utf8'); os.replace(staged, unit)
    manager = ['busctl', '--user', 'call', 'org.freedesktop.systemd1', '/org/freedesktop/systemd1',
               'org.freedesktop.systemd1.Manager']
    subprocess.run(manager + ['Reload'], check=True)
    if args.start:
        subprocess.run(manager + ['RestartUnit', 'ss', 'beamng-earth-voice.service', 'replace'], check=True)
    print('Voice installed. Restart the stopped BeamMP server to load its broker. See README.md for verification.')
    print('For start at boot, enable lingering for this Linux user using your host administrator.')


if __name__ == '__main__': main()
