import struct
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voice


class VoiceTest(unittest.TestCase):
    def test_selected_device_reopens_engine_and_reports_resolved_names(self):
        engines, notices = [], []
        class Engine:
            def __init__(self, input_name, output_name):
                self.names = (input_name or 'Default mic', output_name or 'Default headset')
                self.closed = False
                engines.append(self)
            def selected_devices(self): return self.names
            def close(self): self.closed = True
            def set_ptt(self, mode): pass
            def take_encoded(self): return []
            def microphone_level(self): return (0., False)
            def last_error(self): return ''
        class Client:
            def __init__(self, *args): self.ready = False
            def connect(self): self.ready = True
            def close(self): self.ready = False
            def poll(self): pass
            def set_targets(self, *args): pass
        native = SimpleNamespace(AudioEngine=Engine, device_info=lambda: (
            ['Default mic', 'USB mic'], ['Default headset', 'Speakers'], 'Default mic', 'Default headset'))
        manager = voice.VoiceManager(notices.append)
        def wait_for(condition):
            until = time.monotonic()+3
            while not condition() and time.monotonic()<until: time.sleep(.01)
            self.assertTrue(condition())
        with patch.dict(sys.modules, {'earthvoice': native}), patch.object(voice, 'MumbleClient', Client):
            try:
                manager.handle({'action':'control', 'data':{'enabled':True}})
                manager.handle({'action':'session', 'data':{'base':3000, 'host':'test', 'port':1,
                                                          'fingerprint':'test', 'token':'private'}})
                wait_for(lambda: bool(notices) and notices[-1]['deviceActive'].get('input') == 'Default mic')
                manager.handle({'action':'control', 'data':{'enabled':True, 'input':'USB mic', 'output':'Speakers'}})
                wait_for(lambda: notices[-1]['deviceActive'].get('input') == 'USB mic')
                self.assertTrue(engines[0].closed)
                self.assertEqual(engines[-1].names, ('USB mic', 'Speakers'))
                self.assertEqual(notices[-1]['deviceActive']['output'], 'Speakers')
                # Repeating the same selection must not churn the audio streams.
                manager.handle({'action':'control', 'data':{'enabled':True, 'input':'USB mic', 'output':'Speakers'}})
                manager.handle({'action':'control', 'data':{'enabled':False}})
                wait_for(lambda: notices[-1]['enabled'] is False)
                self.assertEqual(len(engines), 2)
                self.assertTrue(engines[-1].closed)
                self.assertEqual(notices[-1]['deviceActive'], {})
            finally:
                manager.close(); manager.thread.join(3)

    def test_device_enumeration_works_while_muted_without_opening_streams(self):
        notices = []
        def forbidden(*args): raise AssertionError('Enumeration must not open audio streams')
        native = SimpleNamespace(AudioEngine=forbidden, device_info=lambda: (
            ['USB microphone'], ['Headset','Speakers'], 'USB microphone', 'Headset'))
        manager = voice.VoiceManager(notices.append)
        with patch.dict(sys.modules, {'earthvoice': native}):
            try:
                manager.handle({'action':'devices'})
                until=time.monotonic()+3
                while not notices and time.monotonic()<until: time.sleep(.01)
                self.assertTrue(notices)
                self.assertEqual(notices[-1]['deviceDefaults'], {'input':'USB microphone','output':'Headset'})
                self.assertEqual(notices[-1]['outputs'], ['Headset','Speakers'])
                self.assertFalse(notices[-1]['enabled'])
                self.assertEqual(notices[-1]['deviceActive'], {})
            finally:
                manager.close(); manager.thread.join(3)

    def test_global_membership_and_private_mic_test_expire_without_broadcast(self):
        engines, clients, notices = [], [], []

        class Engine:
            def __init__(self, *args): self.mode = 0; engines.append(self)
            def close(self): self.mode = 0
            def set_ptt(self, mode): self.mode = mode
            def take_encoded(self): return [(4, b'private-meter-only')] if self.mode == 4 else []
            def microphone_level(self): return (.2, True) if self.mode == 4 else (0., False)
            def last_error(self): return ''
            def remove(self, uid): pass

        class Client:
            def __init__(self, *args): self.role = args[5]; self.ready = False; self.sent = []; clients.append(self)
            def connect(self): self.ready = True
            def close(self): self.ready = False
            def poll(self): pass
            def set_targets(self, *args): pass
            def send_audio(self, packet, *args, **kw): self.sent.append(packet)

        manager = voice.VoiceManager(notices.append)
        def wait_for(condition):
            until = time.monotonic() + 3
            while not condition() and time.monotonic() < until: time.sleep(.01)
            self.assertTrue(condition())
        native = SimpleNamespace(AudioEngine=Engine, devices=lambda: ([], []))
        with patch.dict(sys.modules, {'earthvoice': native}), patch.object(voice, 'MumbleClient', Client):
            try:
                manager.handle({'action': 'control', 'data': {'enabled': True}})
                manager.handle({'action': 'session', 'data': {'base': 3000, 'host': 'test', 'port': 1,
                                                            'fingerprint': 'test', 'token': 'private'}})
                wait_for(lambda: bool(clients))
                self.assertEqual([c.role for c in clients], ['p'])
                manager.handle({'action': 'control', 'data': {'enabled': True, 'server': True}})
                wait_for(lambda: any(c.role == 's' and c.ready for c in clients))
                manager.handle({'action': 'control', 'data': {'enabled': True, 'server': False}})
                wait_for(lambda: all(not c.ready for c in clients if c.role == 's'))
                manager.handle({'action': 'mic_test', 'data': {'active': True}})
                wait_for(lambda: engines[-1].mode == 4)
                wait_for(lambda: any(n['microphone']['detected'] and n['testing'] for n in notices))
                # With no refresh/release command, the short local lease still stops capture.
                wait_for(lambda: engines[-1].mode == 0)
                self.assertFalse(any(c.sent for c in clients))
            finally:
                manager.close()
                if manager.thread: manager.thread.join(3)
        self.assertFalse(manager.thread.is_alive())

    def test_spatial_geographic_distance_and_pan(self):
        # ECEF differences stay stable when the game moves its local origin.
        pos = (1100000., -4800000., 4000000.)
        left = (pos[0] - 20, pos[1], pos[2])
        gain, pan = voice.spatial(pos, (1,0,0), left)
        self.assertGreater(gain, 0)
        self.assertEqual(pan, -1)
        self.assertEqual(voice.spatial(pos, (1,0,0), (pos[0]+81,*pos[1:]))[0], 0)
        with self.assertRaises(ValueError): voice.vector((1,2,float('nan')))

    def test_disabled_worker_never_opens_audio(self):
        notices = []
        v = voice.VoiceManager(notices.append)
        with patch.dict(sys.modules, {'earthvoice': None}):
            v.handle({'action':'control','data':{'enabled':False}})
            until = time.monotonic()+2
            while not notices and time.monotonic()<until: time.sleep(.01)
            v.close(); v.thread.join(2)
        self.assertTrue(notices)
        self.assertFalse(notices[0]['enabled'])
        self.assertEqual(notices[0]['error'], '')
        self.assertFalse(v.thread.is_alive())

    def test_native_headless_codec(self):
        import earthvoice
        codec = earthvoice.OpusCodec()
        packet = codec.encode(struct.pack('<960f', *[0.1]*960))
        self.assertLess(len(packet),512)
        self.assertEqual(len(codec.decode(packet)),960*4)


if __name__ == '__main__': unittest.main()
