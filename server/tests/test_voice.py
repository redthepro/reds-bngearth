import struct
import sys
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import voice


class VoiceTest(unittest.TestCase):
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
