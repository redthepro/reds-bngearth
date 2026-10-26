#!/usr/bin/env python3
"""Generate private host configuration once; never print credentials."""
import argparse
import hashlib
import os
from pathlib import Path
import secrets
import ssl
import subprocess

from policy import atomic_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--game', type=Path, required=True)
    parser.add_argument('--host', required=True)
    parser.add_argument('--port', type=int, default=64738, help='Published host port; internal listener stays 64738')
    args = parser.parse_args()
    os.umask(0o077)
    args.data.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.game.mkdir(parents=True, exist_ok=True, mode=0o700)
    secret = args.data / 'ice-secret'
    if not secret.exists(): secret.write_text(secrets.token_hex(32), encoding='ascii')
    cert, key = args.data / 'cert.pem', args.data / 'key.pem'
    if cert.exists() != key.exists():
        raise ValueError('TLS key/certificate is incomplete; restore the matching pair from backup before continuing.')
    if not cert.exists():
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:3072', '-nodes', '-days', '3650',
                        '-subj', '/CN=BeamNG.Earth voice', '-keyout', str(key), '-out', str(cert)],
                       check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    ini = args.data / 'mumble.ini'
    if not ini.exists():
        ini.write_text('\n'.join([
            'database=/data/mumble.sqlite', 'logfile=', 'host=0.0.0.0', 'port=64738',
            'users=300', 'bandwidth=72000', 'certrequired=false', 'opusthreshold=0',
            'registerName=BeamNG.Earth voice', 'welcometext=BeamNG.Earth opt-in voice',
            'serverpassword=' + secrets.token_urlsafe(48),
            'sslCert=/data/cert.pem', 'sslKey=/data/key.pem',
            'ice="tcp -h 127.0.0.1 -p 6502"',
            'icesecretread=' + secret.read_text().strip(),
            'icesecretwrite=' + secret.read_text().strip(), '',
        ]), encoding='utf8')
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
    atomic_json(args.game / 'endpoint.json', {'host': args.host, 'port': args.port, 'fingerprint': fingerprint})
    print('Voice configuration ready; TLS fingerprint:', fingerprint)


if __name__ == '__main__':
    main()
