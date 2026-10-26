"""Opt-in native audio worker, independent of terrain generation and game sockets."""
import math
import queue
import threading
import time

from voice_client import MumbleClient

ROLES = ('p', 's', 'g')


def spatial(listener, right, source, radius=80):
    if not listener or not source:
        return 0., 0.
    delta = [b - a for a, b in zip(listener, source)]
    distance = math.sqrt(sum(v * v for v in delta))
    gain = max(0., 1 - max(0., distance - 3) / (radius - 3)) ** 2
    pan = sum(a * b for a, b in zip(delta, right)) / max(1., distance)
    return gain, max(-1., min(1., pan))


def vector(value):
    if not isinstance(value, (tuple, list)) or len(value) != 3:
        raise ValueError('invalid voice vector')
    if not all(isinstance(v, (int, float)) and math.isfinite(v) and abs(v) <= 7e6 for v in value):
        raise ValueError('invalid voice coordinate')
    return tuple(float(v) for v in value)


class VoiceManager:
    def __init__(self, notify):
        self.notify = notify
        self.commands = queue.Queue(maxsize=64)
        self.stop = threading.Event()
        self.thread = None

    def handle(self, message):
        if self.thread is None:
            self.thread = threading.Thread(target=self._run, name='earth-voice', daemon=True)
            self.thread.start()
        if not isinstance(message, dict) or message.get('action') not in ('session', 'state', 'control', 'pose', 'ptt', 'reset'):
            raise ValueError('invalid voice action')
        try:
            self.commands.put_nowait(dict(message))
        except queue.Full:
            # Fail closed; no stale PTT events should accumulate behind a stalled login.
            self.close()
            raise ValueError('voice worker stalled')

    def close(self):
        self.stop.set()

    def _run(self):
        engine = None
        clients = {}
        session, state, options = {}, {}, {'enabled': False, 'server': False,
                                            'volumes': [1., 1., 1.], 'muted': []}
        listener, right = None, (1., 0., 0.)
        held, held_at, state_at, mode = '', 0., 0., ''
        retry_at, status_at = 0., 0.
        error = ''
        speaking = {}
        native = None
        last_status = None

        def status():
            nonlocal last_status
            for uid, at in list(speaking.items()):
                if time.monotonic() - at > 1: speaking.pop(uid, None)
            microphone = {'active': False, 'level': 0., 'detected': False}
            if engine:
                level, active = engine.microphone_level()
                level = max(0., min(1., float(level)))
                microphone = {'active': bool(active), 'level': level,
                              'detected': bool(active and level >= .012)}
            message = {'type': 'voice_status', 'enabled': options['enabled'], 'error': error,
                         'connected': [r for r, c in clients.items() if c.ready],
                         'talk': mode, 'speaking': [uid for uid, at in speaking.items() if time.monotonic()-at < .3],
                         'inputs': inputs, 'outputs': outputs, 'microphone': microphone}
            if message != last_status:
                self.notify(message)
                last_status = message

        inputs, outputs = [], []
        def remove(uid):
            if engine:
                engine.remove(uid)
            speaking.pop(uid, None)

        def receive(role, uid, seq, packet, pos, end):
            if not engine or (uid - ROLES.index(role)) in options['muted']:
                return
            # Registered role IDs must match our current game roster.
            offset = ROLES.index(role)
            if not any(v.get('base', -999) + offset == uid for v in state.get('roster', {}).values()):
                return
            gain, pan = spatial(listener, right, pos) if role == 'p' else (1., 0.)
            gain *= options['volumes'][offset]
            if packet:
                try:
                    engine.push_packet(uid, seq, packet, gain, pan)
                except (ValueError, RuntimeError):
                    remove(uid)
                    return
                speaking[uid] = time.monotonic()
            if end:
                engine.finish(uid)

        try:
            while not self.stop.wait(.01):
                now = time.monotonic()
                try:
                    for _ in range(64):
                        try:
                            msg = self.commands.get_nowait()
                        except queue.Empty:
                            break
                        action = msg['action']
                        if action == 'session':
                            data = msg.get('data', {})
                            if not isinstance(data, dict) or not isinstance(data.get('base'), int):
                                raise ValueError('invalid voice session')
                            if data != session:
                                session = dict(data)
                                for c in clients.values(): c.close()
                                clients.clear()
                                held = ''
                        elif action == 'state':
                            data = msg.get('data', {})
                            if not isinstance(data, dict) or len(data.get('roster', {})) > 100:
                                raise ValueError('invalid voice state')
                            if data.get('group') != state.get('group') and 'g' in clients:
                                clients.pop('g').close()
                            if 'roster' not in data:
                                data['roster'] = state.get('roster', {})
                            state = data
                            state_at = now
                        elif action == 'control':
                            data = msg.get('data', {})
                            options['enabled'] = data.get('enabled') is True
                            options['server'] = data.get('server') is True
                            volumes = data.get('volumes', [1., 1., 1.])
                            if len(volumes) != 3 or not all(isinstance(v, (int,float)) and math.isfinite(v) for v in volumes):
                                raise ValueError('invalid voice volumes')
                            options['volumes'] = [max(0., min(2., v)) for v in volumes]
                            muted = data.get('muted', []) or []
                            options['muted'] = [int(uid) for uid in muted[:300]]
                            if engine:
                                for base in options['muted']:
                                    for offset in range(3): remove(base + offset)
                            device_changed = any(data.get(k) != options.get(k) for k in ('input', 'output'))
                            options.update({k: str(data.get(k, ''))[:512] for k in ('input', 'output')})
                            if device_changed and engine:
                                engine.close(); engine = None
                        elif action == 'pose':
                            listener, right = vector(msg.get('pos')), vector(msg.get('right'))
                        elif action == 'ptt':
                            held = msg.get('talk', '')
                            if held not in ROLES: held = ''
                            held_at = now
                        elif action == 'reset':
                            held, session, state = '', {}, {}
                    enabled = options['enabled'] and bool(session)
                    if not enabled:
                        for c in clients.values(): c.close()
                        clients.clear()
                        if engine:
                            engine.close(); engine = None
                        mode = ''
                    elif now >= retry_at:
                        if native is None:
                            import earthvoice as native
                            inputs, outputs = native.devices()
                        if engine is None:
                            engine = native.AudioEngine(options['input'], options['output'])
                        roles = ['p']
                        if options['server']:
                            roles.append('s')
                        if state.get('group'):
                            roles.append('g')
                        for role in tuple(clients):
                            if role not in roles:
                                clients.pop(role).close()
                        for role in roles:
                            if role not in clients:
                                c = MumbleClient(session['host'], int(session['port']), session['fingerprint'],
                                                 'u' + str(session['base']) + role, session['token'], role,
                                                 lambda *args, r=role: receive(r, *args), remove)
                                c.connect()
                                clients[role] = c
                        error = ''
                    desired = held if now - held_at < .3 and now - state_at < 1 and state.get('talk') == held else ''
                    if desired not in clients or not clients[desired].ready:
                        desired = ''
                    if engine:
                        if mode and desired != mode and mode in clients:
                            clients[mode].send_audio(b'', end=True)
                        mode = desired
                        engine.set_ptt(ROLES.index(mode) + 1 if mode else 0)
                        if 'p' in clients:
                            clients['p'].set_targets(set((state.get('nearby', []) or [])[:100]))
                        for encoded_mode, packet in engine.take_encoded():
                            role = ROLES[encoded_mode - 1]
                            if role == mode and role in clients:
                                clients[role].send_audio(packet, listener if role == 'p' else None)
                    for c in clients.values(): c.poll()
                    if engine and engine.last_error():
                        raise RuntimeError('audio device disconnected')
                except Exception as exc:
                    # Never include exception text: network errors may contain credentials.
                    error = 'Voice unavailable (' + type(exc).__name__ + ')'
                    for c in clients.values(): c.close()
                    clients.clear()
                    if engine:
                        engine.close(); engine = None
                    held, mode, retry_at = '', '', now + 3
                if now - status_at > .25:
                    status(); status_at = now
        finally:
            for c in clients.values(): c.close()
            if engine: engine.close()
