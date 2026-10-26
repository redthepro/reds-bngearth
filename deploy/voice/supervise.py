#!/usr/bin/env python3
"""Treat authenticator and public listener as one fail-closed service."""
import os
import signal
import subprocess
import time

from policy import read_json

children = []
stopping = False


def stop(*args):
    global stopping
    stopping = True


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)
root = os.environ.get('VOICE_DATA', '/data')
try:
    children.append(subprocess.Popen(['/usr/bin/mumble-server', '-fg', '-ini', root + '/mumble.ini']))
    time.sleep(1)
    children.append(subprocess.Popen(['/usr/bin/python3', '/opt/voice/controller.py']))
    started = time.time()
    while not stopping and all(p.poll() is None for p in children):
        if time.time() - started > 10:
            try:
                healthy = time.time() - read_json(root + '/heartbeat.json')['time'] < 5
            except (OSError, ValueError, KeyError):
                healthy = False
            if not healthy:
                break
        time.sleep(0.25)
finally:
    for p in reversed(children):
        if p.poll() is None:
            p.terminate()
    for p in children:
        try:
            p.wait(timeout=5)
        except subprocess.TimeoutExpired:
            p.kill()
    raise SystemExit(0 if stopping else 1)
