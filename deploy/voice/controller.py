#!/usr/bin/env python3
"""Private Ice authenticator and role/channel enforcement. No public HTTP API."""
import hashlib
import os
import threading
import time

import Ice
from policy import Registry, atomic_json, read_json

HERE = os.path.dirname(os.path.abspath(__file__))
Ice.loadSlice('-I/usr/share/ice/slice ' + HERE + '/vendor/MumbleServer.ice')
import MumbleServer as M

ROOT = os.environ.get('VOICE_DATA', '/data')
GAME = os.environ.get('VOICE_GAME', '/game')
LOCK = threading.RLock()
registry = Registry()


class Auth(M.ServerAuthenticator):
    def authenticate(self, name, pw, certificates, certhash, certstrong, current=None):
        with LOCK:
            return registry.authenticate(name, pw)

    def getInfo(self, id, current=None):
        with LOCK:
            found = registry.by_uid(id)
            if found:
                _, identity, role = found
                return True, {M.UserName: 'u' + str(identity['base']) + role}
        return False, {}

    def nameToId(self, name, current=None):
        with LOCK:
            found = registry.account(name)
            return found[3] if found else -2

    def idToName(self, id, current=None):
        ok, info = self.getInfo(id)
        return info.get(M.UserName, '') if ok else ''

    def idToTexture(self, id, current=None):
        return b''


def acl(group, allow=0, deny=0):
    return M.ACL(True, True, False, -1, group, allow, deny)


def configure(server):
    # Include Listen (0x800): outsiders must not subscribe to a private channel
    # without entering it. Keep root as a silent staging channel.
    server.setACL(0, [acl('all', 0x06, 0x1ff7f9)], [], False)
    channels = {c.name: id for id, c in server.getChannels().items()}
    return channels


def channel(server, channels, name, group, proximity_uid=None):
    if name not in channels:
        id = server.addChannel(name, 0)
        rules = [acl('all', 0x02, 0x1ffffd)]
        if proximity_uid is not None:
            # Mumble suppresses ALL audio when Speak is denied, including whispers.
            # Give each p connection its own speaking room; only its owner can enter.
            rules += [acl('role_p', 0x102), M.ACL(True, True, False, proximity_uid, '', 0x0e, 0)]
        else:
            rules += [acl(group, 0x0e)]
        server.setACL(id, rules, [], False)
        channels[name] = id
    return channels[name]


def run():
    secret = open(ROOT + '/ice-secret', encoding='ascii').read().strip()
    props = Ice.createProperties()
    props.setProperty('Ice.ImplicitContext', 'Shared')
    props.setProperty('Ice.Default.InvocationTimeout', '2000')
    props.setProperty('Ice.ThreadPool.Server.Size', '2')
    init = Ice.InitializationData()
    init.properties = props
    with Ice.initialize(init) as communicator:
        communicator.getImplicitContext().put('secret', secret)
        for attempt in range(40):
            try:
                meta = M.MetaPrx.checkedCast(communicator.stringToProxy('Meta:tcp -h 127.0.0.1 -p 6502'))
                server = meta.getServer(1)
                server.getUsers()
                break
            except Ice.Exception:
                if attempt == 39: raise
                time.sleep(.2)
        adapter = communicator.createObjectAdapterWithEndpoints('VoiceAuth', 'tcp -h 127.0.0.1 -p 6503')
        auth = M.ServerAuthenticatorPrx.uncheckedCast(adapter.addWithUUID(Auth()))
        adapter.activate()
        # Never call Ice server methods inside an authenticator callback: deadlock.
        channels = configure(server)
        channel(server, channels, 'Server', 'role_s')
        server.setAuthenticator(auth)
        known_groups = {}
        last_credentials = None
        print('Voice authenticator ready', flush=True)
        while True:
            try:
                state = read_json(GAME + '/state.json')
                with LOCK:
                    registry.update(state)
            except (OSError, ValueError, TypeError):
                # Retain credentials briefly over atomic writes; expire after 5s.
                pass
            fresh = registry.fresh()
            credentials = registry.credentials()
            if credentials != last_credentials:
                atomic_json(GAME + '/credentials.json', credentials)
                last_credentials = credentials
            for identity in registry.identities.values():
                channel(server, channels, 'Nearby ' + str(identity['base']), 'role_p', identity['base'])
                group = identity['group']
                if group:
                    key = hashlib.sha256(group.encode()).hexdigest()[:24]
                    channel(server, channels, 'Group ' + key, 'party_' + key)
            desired_channels = {'Nearby ' + str(i['base']) for i in registry.identities.values()}
            desired_channels |= {'Group ' + hashlib.sha256(i['group'].encode()).hexdigest()[:24]
                                 for i in registry.identities.values() if i['group']}
            active_sessions = set()
            for session, user in server.getUsers().items():
                found = registry.by_uid(user.userid) if fresh else None
                if not found:
                    server.kickUser(session, 'Game session ended')
                    continue
                pid, identity, role = found
                group = identity['group'] if role == 'g' else ''
                active_sessions.add(session)
                if session in known_groups and known_groups[session] != group:
                    server.kickUser(session, 'Voice group changed; reconnect')
                    continue
                known_groups[session] = group
                name = 'Nearby ' + str(identity['base']) if role == 'p' else 'Server'
                if role == 'g':
                    name = 'Group ' + hashlib.sha256(group.encode()).hexdigest()[:24]
                muted = not registry.allowed_to_talk(pid, role)
                if user.channel != channels[name] or user.mute != muted:
                    user.channel, user.mute = channels[name], muted
                    server.setState(user)
            known_groups = {s: g for s, g in known_groups.items() if s in active_sessions}
            for name in list(channels):
                if name.startswith(('Nearby ', 'Group ')) and name not in desired_channels:
                    server.removeChannel(channels.pop(name))
            # Supervisor kills listener if this heartbeat stops, even with existing logins.
            atomic_json(ROOT + '/heartbeat.json', {'time': time.time()})
            time.sleep(0.1)


if __name__ == '__main__':
    run()
