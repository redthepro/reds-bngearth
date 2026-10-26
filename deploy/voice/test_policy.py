import time
import unittest
from policy import Registry


class RegistryTest(unittest.TestCase):
    def state(self, epoch='a', group='', talk=''):
        return {'time': time.time(), 'players': {'1': {'epoch': epoch, 'name': 'Player', 'group': group, 'talk': talk}}}

    def test_credentials_rotate_on_rejoin(self):
        r = Registry()
        r.update(self.state())
        first = dict(r.identities['1'])
        r.update(self.state())
        self.assertEqual(first, r.identities['1'])
        name = 'u' + str(first['base']) + 'p'
        self.assertEqual(r.authenticate(name, first['token'])[0], first['base'])
        self.assertEqual(r.authenticate(name, 'wrong')[0], -1)
        r.update(self.state(epoch='b'))
        self.assertEqual(r.authenticate(name, first['token'])[0], -1)
        self.assertNotEqual(r.identities['1']['token'], first['token'])

    def test_private_group_and_role(self):
        r = Registry()
        r.update(self.state())
        ident = r.identities['1']
        name = 'u' + str(ident['base']) + 'g'
        self.assertEqual(r.authenticate(name, ident['token'])[0], -1)
        r.update(self.state(group='secret', talk='g'))
        uid, _, groups = r.authenticate(name, ident['token'])
        self.assertEqual(uid, ident['base'] + 2)
        self.assertIn('role_g', groups)
        self.assertTrue(any(g.startswith('party_') for g in groups))
        self.assertTrue(r.allowed_to_talk('1', 'g'))
        self.assertFalse(r.allowed_to_talk('1', 's'))
        r.update(self.state())
        self.assertIsNone(r.by_uid(uid))

    def test_stale_and_disconnected_are_denied(self):
        r = Registry()
        r.update(self.state())
        ident = r.identities['1']
        name = 'u' + str(ident['base']) + 'p'
        r.updated -= 6
        self.assertEqual(r.authenticate(name, ident['token'])[0], -1)
        self.assertFalse(r.allowed_to_talk('1', 'p'))
        with self.assertRaises(ValueError):
            r.update({'time': 0, 'players': {}})
        r.update({'time': time.time(), 'players': {}})
        self.assertEqual(r.authenticate(name, ident['token'])[0], -1)


if __name__ == '__main__':
    unittest.main()
