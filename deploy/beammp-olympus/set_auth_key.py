#!/usr/bin/env python3
"""Enter the BeamMP key privately on Olympus, then start its isolated user service."""
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import tomllib

CONFIG = Path(__file__).resolve().parent / "data" / "ServerConfig.toml"


def valid(key):
    return isinstance(key, str) and re.fullmatch(r"[A-Za-z0-9-]{20,128}", key) is not None


def main():
    if sys.argv[1:] == ["--check"]:
        try:
            key = tomllib.loads(CONFIG.read_text())["General"]["AuthKey"]
            return 0 if valid(key) else 1
        except (OSError, KeyError, ValueError):
            return 1
    from_stdin = sys.argv[1:] == ["--stdin"]
    if sys.argv[1:] and not from_stdin:
        raise SystemExit("No key arguments are accepted. Run interactively over ssh -t.")
    if from_stdin and sys.stdin.isatty():
        raise SystemExit("Use the default hidden prompt for interactive entry.")
    if not from_stdin and not sys.stdin.isatty():
        raise SystemExit("Use ssh -t olympus ~/beamng-earth-server/set_auth_key.py for a hidden prompt.")
    key = (sys.stdin.read(256) if from_stdin else getpass.getpass("BeamMP AuthKey (hidden): ")).strip()
    if not valid(key):
        raise SystemExit("Key format was not accepted; the saved configuration was not changed.")
    text = CONFIG.read_text()
    replacement = "AuthKey = " + json.dumps(key)
    updated, count = re.subn(r"(?m)^AuthKey\s*=.*$", lambda _: replacement, text)
    if count != 1:
        raise SystemExit("Expected one AuthKey field; configuration was not changed.")
    tomllib.loads(updated)
    fd, temporary = tempfile.mkstemp(prefix=".auth-", dir=CONFIG.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(updated)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, CONFIG)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    key = replacement = updated = None
    print("Authentication key saved privately. Starting BeamMP.Earth...")
    subprocess.run(["systemctl", "--user", "reset-failed", "beammp-earth.service"], check=False)
    subprocess.run(["systemctl", "--user", "restart", "beammp-earth.service"], check=True)
    subprocess.run(["systemctl", "--user", "is-active", "beammp-earth.service"], check=False)
    print("Use journalctl --user -u beammp-earth.service to check startup; do not share the authentication key.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
