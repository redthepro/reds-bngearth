#!/usr/bin/env python3
"""Explicit headless live voice test (synthetic tone; never opens audio devices).

Run against an isolated voice registry BEFORE installing the live BeamMP plugin.
Uses ssh olympus; do not run once real players are using the voice service.
"""
import json
from pathlib import Path
import struct
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'server'))
from voice_client import MumbleClient
import earthvoice

REMOTE = 'beamng-earth-server/data/voice/'


def remote_read(name):
    data = subprocess.check_output(['ssh', 'olympus', 'cat ' + REMOTE + name])
    return json.loads(data)


def main():
    if '--isolated-registry' not in sys.argv:
        raise SystemExit('Pass --isolated-registry only before enabling the live game plugin.')
    seed = '''import json,os,time
from pathlib import Path
p=Path.home()/'beamng-earth-server/data/voice/state.json'
(p.parent/'test-active').touch()
for _ in range(600):
 if not (p.parent/'test-active').exists(): break
 d={'time':time.time(),'players':{str(i):{'epoch':'test-'+str(i),'name':'Synthetic'+str(i),'group':'A' if i<3 else '', 'talk':role} for i,role in [(1,'p'),(2,'p'),(3,'p')]}}
 # Permit only this test process to change role while keeping a fresh game heartbeat.
 try: role=(p.parent/'test-role').read_text().strip()
 except OSError: role='p'
 for player in d['players'].values(): player['talk']=role
 tmp=p.with_suffix('.new');tmp.write_text(json.dumps(d));os.replace(tmp,p);time.sleep(.1)
'''
    writer = subprocess.Popen(['ssh', 'olympus', 'python3 -u -'], stdin=subprocess.PIPE,
                              stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    writer.stdin.write(seed.encode()); writer.stdin.close()
    clients, received = {}, {}
    try:
        time.sleep(1)
        endpoint = remote_read('endpoint.json')
        credentials = remote_read('credentials.json')['players']
        for pid in ('1','2','3'):
            ident = credentials[pid]
            for role in ('p','s','g') if pid != '3' else ('p','s'):
                key = (pid,role)
                received[key] = []
                c = MumbleClient('192.168.86.100', 64738, endpoint['fingerprint'],
                                 'u'+str(ident['base'])+role, ident['token'], role,
                                 lambda *args,k=key: received[k].append(args), lambda *args: None)
                c.connect(); clients[key] = c

        def poll(seconds):
            until = time.monotonic()+seconds
            while time.monotonic()<until:
                for c in clients.values(): c.poll()
                time.sleep(.01)

        poll(2)
        assert all(c.ready for c in clients.values()), 'authentication/sync failed'
        codec = earthvoice.OpusCodec()
        packet = codec.encode(struct.pack('<960f', *[.1]*960))

        def test(role, expected, targets=None):
            subprocess.run(['ssh','olympus', 'printf ' + role + ' > ' + REMOTE + 'test-role'], check=True)
            poll(.5)
            for rows in received.values(): rows.clear()
            sender = clients['1',role]
            if targets is not None: sender.set_targets(targets)
            poll(.2)
            for _ in range(8):
                sender.send_audio(packet, (1100000.,-4800000.,4000000.) if role=='p' else None)
                poll(.02)
            sender.send_audio(b'',end=True)
            poll(.5)
            got = {key for key,rows in received.items() if any(row[2] for row in rows)}
            assert got == expected, (role,got,expected)
            assert all(len(codec.decode(row[2])) == 3840 for key in got for row in received[key] if row[2])
            print(role, 'routing and Opus decode passed', flush=True)

        test('p', {('2','p')}, {credentials['2']['base']})
        test('s', {('2','s'),('3','s')})
        test('g', {('2','g')})
        # A valid outsider account cannot enter the private group's channel.
        outsider_client = clients['3','s']
        group_channel = next(id for id,name in outsider_client.channels.items() if name.startswith('Group '))
        import voice_wire as w
        outsider_client.queue(w.USER, w.pb((1, outsider_client.session), (5, group_channel)))
        poll(.2)
        test('g', {('2','g')})
        # An outsider's g login must fail even with its valid game token.
        outsider = credentials['3']
        bad = MumbleClient('192.168.86.100',64738,endpoint['fingerprint'],
                           'u'+str(outsider['base'])+'g',outsider['token'],'g',lambda *a:None,lambda *a:None)
        try:
            bad.connect()
            rejected = False
            until = time.monotonic()+3
            while time.monotonic()<until:
                try: bad.poll()
                except ConnectionError: rejected=True;break
                time.sleep(.01)
            assert rejected, 'outsider group login accepted'
            print('private group outsider rejected',flush=True)
        finally: bad.close()
    finally:
        for c in clients.values(): c.close()
        subprocess.run(['ssh','olympus','rm -f ' + REMOTE + 'test-active'], check=True)
        try: writer.wait(timeout=5)
        except subprocess.TimeoutExpired:
            writer.terminate(); writer.wait(timeout=5)
        subprocess.run(['ssh','olympus','rm -f ' + REMOTE + 'test-role ' + REMOTE + 'state.json'], check=True)


if __name__ == '__main__': main()
