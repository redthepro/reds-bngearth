#!/usr/bin/env python3
"""Install/update the Earth resources in an existing, stopped BeamMP server.

Run this from the unpacked BeamNG.Earth-BeamMP-Server release. Python 3.10+;
no pip packages, administrator privileges, or changes to ServerConfig.toml.
"""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import zipfile


def resource_files(package):
    resources = package / 'Resources'
    client = resources / 'Client' / 'beamng_earth.zip'
    plugin = resources / 'Server' / 'BeamNGEarth'
    if not client.is_file() or not (plugin / 'main.lua').is_file():
        raise ValueError('Unpack the complete BeamNG.Earth BeamMP server release beside this installer.')
    with zipfile.ZipFile(client) as archive:
        if archive.testzip() or 'lua/ge/extensions/beamngEarth/core.lua' not in archive.namelist():
            raise ValueError('The client mod ZIP is incomplete or corrupt.')
    files = [client] + sorted(p for p in plugin.rglob('*') if p.is_file())
    for path in files:
        if path.is_symlink() or not path.resolve().is_relative_to(resources.resolve()):
            raise ValueError('Refusing a package with resources outside its directory.')
    return [(p, p.relative_to(package)) for p in files]


def install_resources(package, target, dry_run=False):
    package, target = package.resolve(), target.resolve()
    if not target.is_dir() or not (target / 'ServerConfig.toml').is_file():
        raise ValueError('Run BeamMP once to create ServerConfig.toml, stop it, then choose its directory.')
    if target == package or target == Path(target.anchor) or target == Path.home().resolve():
        raise ValueError('Choose the existing BeamMP server directory, not the package or a home/root directory.')
    files = resource_files(package)
    # Never follow a destination symlink into another deployment.
    for _, rel in files:
        if (target / rel).is_symlink() or not (target / rel).resolve().is_relative_to(target):
            raise ValueError('A destination resource points outside the server directory.')
        for parent in (target / rel).parents:
            if parent == target: break
            if parent.is_symlink(): raise ValueError('A resource directory is a symlink.')
    try:
        import tomllib
        cfg = tomllib.loads((target / 'ServerConfig.toml').read_text(encoding='utf-8-sig'))
        port = int(cfg.get('General', {}).get('Port', 30814))
    except ImportError:
        port = 30814
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=.5):
            raise ValueError('The game port is accepting connections. Stop BeamMP before installing resources.')
    except OSError:
        pass
    version = (package / 'VERSION').read_text(encoding='utf-8-sig').strip()
    print(f'BeamNG.Earth {version}: {len(files)} resource files -> {target}')
    if dry_run:
        print('Dry run: no files changed. Existing server configuration and other mods are preserved.')
        return
    backup = target / 'backups' / ('earth-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S-%f'))
    backup.mkdir(parents=True)
    changes = []
    try:
        for source, rel in files:
            dest = target / rel
            existed = dest.exists()
            if existed:
                saved = backup / rel
                saved.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dest, saved)
            dest.parent.mkdir(parents=True, exist_ok=True)
            staged = dest.with_name(dest.name + '.earth-new')
            shutil.copyfile(source, staged)
            if hashlib.sha256(staged.read_bytes()).digest() != hashlib.sha256(source.read_bytes()).digest():
                raise OSError('Resource verification failed.')
            os.replace(staged, dest)
            changes.append((dest, rel, existed))
    except Exception:
        for dest, rel, existed in reversed(changes):
            if existed: shutil.copy2(backup / rel, dest)
            else: dest.unlink(missing_ok=True)
        raise
    (backup / 'installed.json').write_text(json.dumps({'version': version,
        'files': [str(rel) for _, rel in files]}, indent=2), encoding='utf8')
    print(f'Installed {version}. Previous resources: {backup}')
    print('ServerConfig.toml and its AuthKey were left untouched. Start BeamMP when ready.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--server-dir', type=Path, required=True)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--enable-voice', action='store_true', help='Install the optional Linux rootless Podman voice service')
    parser.add_argument('--public-host', help='Public IP/DNS clients use for voice (required with --enable-voice)')
    parser.add_argument('--registry-dir', type=Path, help='Shared private registry; default SERVER/voice for a bare-host game')
    parser.add_argument('--start-voice', action='store_true', help='Start/enable the generated voice service')
    args = parser.parse_args()
    package = Path(__file__).resolve().parent
    if args.start_voice and not args.enable_voice: parser.error('--start-voice requires --enable-voice')
    voice = None
    if args.enable_voice:
        if not args.public_host: parser.error('--public-host is required with --enable-voice')
        voice = [sys.executable, str(package / 'VoiceService' / 'install.py'),
                 '--game-dir', str(args.server_dir.resolve()), '--public-host', args.public_host]
        if args.registry_dir: voice += ['--registry-dir', str(args.registry_dir.resolve())]
        # Check prerequisites before replacing any live resources.
        subprocess.run(voice + ['--dry-run'], check=True)
    install_resources(package, args.server_dir, args.dry_run)
    if voice is not None and not args.dry_run:
        subprocess.run(voice + (['--start'] if args.start_voice else []), check=True)


if __name__ == '__main__':
    try: main()
    except (ValueError, OSError, zipfile.BadZipFile, subprocess.CalledProcessError) as exc:
        print('Install failed:', exc, file=sys.stderr)
        sys.exit(1)
