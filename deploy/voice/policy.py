"""Voice identities derive exclusively from the private BeamMP player registry."""
import hashlib
import hmac
import json
import math
import os
import secrets
import time

ROLES = ('p', 's', 'g')


def atomic_json(path, value):
    tmp = str(path) + '.new'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w', encoding='utf8') as f:
        json.dump(value, f, separators=(',', ':'), allow_nan=False)
    os.replace(tmp, path)


def read_json(path, limit=262144):
    with open(path, 'rb') as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise ValueError('oversized voice registry')
    return json.loads(data)


class Registry:
    def __init__(self):
        self.players = {}
        self.identities = {}
        self.updated = 0.
        self.serial = secrets.randbelow(100000000) * 3 + 3000

    def update(self, state, now=None):
        now = time.time() if now is None else now
        if not isinstance(state, dict) or abs(now - float(state.get('time', 0))) > 5:
            raise ValueError('stale game registry')
        players = state.get('players')
        if not isinstance(players, dict) or len(players) > 100:
            raise ValueError('invalid player registry')
        clean, identities = {}, {}
        for pid, player in players.items():
            if not isinstance(pid, str) or not pid.isdigit() or not isinstance(player, dict):
                raise ValueError('invalid player identity')
            epoch = player.get('epoch')
            group = player.get('group', '')
            if not isinstance(epoch, str) or not 1 <= len(epoch) <= 100:
                raise ValueError('invalid session epoch')
            if not isinstance(group, str) or len(group) > 100:
                raise ValueError('invalid group')
            old = self.identities.get(pid)
            if not old or old['epoch'] != epoch:
                self.serial += 3
                old = dict(epoch=epoch, base=self.serial, token=secrets.token_urlsafe(32))
            identity = dict(old, group=group, name=str(player.get('name', pid))[:80])
            clean[pid], identities[pid] = dict(player), identity
        self.players, self.identities, self.updated = clean, identities, min(now, float(state['time']))

    def fresh(self, now=None):
        return (time.time() if now is None else now) - self.updated < 5

    def credentials(self):
        return {'players': self.identities}

    def account(self, name):
        if not self.fresh() or not isinstance(name, str):
            return None
        for pid, identity in self.identities.items():
            for offset, role in enumerate(ROLES):
                if name == 'u' + str(identity['base']) + role and (role != 'g' or identity['group']):
                    return pid, identity, role, identity['base'] + offset
        return None

    def authenticate(self, name, token):
        account = self.account(name)
        if not account or not isinstance(token, str) or len(token) > 128:
            return -1, '', []
        _, identity, role, uid = account
        if not hmac.compare_digest(identity['token'], token):
            return -1, '', []
        group = 'party_' + hashlib.sha256(identity['group'].encode()).hexdigest()[:24]
        return uid, name, ['role_' + role] + ([group] if role == 'g' else [])

    def by_uid(self, uid):
        for pid, identity in self.identities.items():
            offset = uid - identity['base']
            if 0 <= offset < 3:
                role = ROLES[offset]
                if role == 'g' and not identity['group']:
                    return None
                return pid, identity, role
        return None

    def allowed_to_talk(self, pid, role):
        return self.fresh() and self.players.get(pid, {}).get('talk', '') == role
