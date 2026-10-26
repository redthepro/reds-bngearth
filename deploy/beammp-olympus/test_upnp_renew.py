"""Network-safety checks for the standalone UPnP renewal helper; no real router used."""
import importlib.util
import io
from pathlib import Path
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('upnp_renew', Path(__file__).with_name('upnp-renew.py'))
upnp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upnp)


class FakeGateway:
    def __init__(self, existing=None):
        self.existing, self.writes, self.reads = existing or {}, [], []

    def mapping(self, port, protocol):
        self.reads.append(protocol)
        return self.existing.get(protocol)

    def call(self, action, arguments):
        if action == 'GetExternalIPAddress':
            return {'NewExternalIPAddress': '203.0.113.1'}
        assert action == 'AddPortMapping'
        self.writes.append(arguments)
        self.existing[arguments['NewProtocol']] = {k: str(v) for k, v in arguments.items()}
        return {}


def mapping(**changes):
    return dict(NewInternalClient='192.168.86.100', NewInternalPort='30814',
                NewPortMappingDescription=upnp.DESCRIPTION, NewEnabled='1', NewLeaseDuration='600', **changes)


class ExpiringGateway(FakeGateway):
    """Models the router's no-op renewal and failures after a successful deletion."""
    def __init__(self, failures=0, foreign_on_retry=False, lost_success_reply=False):
        soon, permanent = mapping(), mapping()
        soon['NewLeaseDuration'], permanent['NewLeaseDuration'] = '1800', '0'
        super().__init__({'TCP': soon, 'UDP': permanent})
        self.failures, self.foreign_on_retry = failures, foreign_on_retry
        self.lost_success_reply = lost_success_reply
        self.deletions, self.restores, self.after_failure_reads = 0, 0, 0

    def mapping(self, port, protocol):
        if protocol == 'TCP' and self.foreign_on_retry and self.restores:
            self.after_failure_reads += 1
            if self.after_failure_reads == 2:
                foreign = mapping()
                foreign['NewInternalClient'] = '192.168.86.200'
                self.existing['TCP'] = foreign
        return super().mapping(port, protocol)

    def call(self, action, arguments):
        protocol = arguments.get('NewProtocol')
        if action == 'DeletePortMapping':
            self.deletions += 1
            del self.existing[protocol]
            return {}
        if action == 'AddPortMapping':
            if protocol in self.existing:
                return {}  # Existing leases are not extended by this router.
            self.restores += 1
            if self.failures:
                self.failures -= 1
                raise OSError('temporary router failure after deletion')
            result = super().call(action, arguments)
            if self.lost_success_reply:
                raise TimeoutError('router applied mapping but response was lost')
            return result
        return super().call(action, arguments)


class RenewalTests(unittest.TestCase):
    def test_conflicting_udp_cannot_create_tcp_mapping(self):
        row = mapping()
        row['NewInternalClient'] = '192.168.86.200'
        gateway = FakeGateway({'UDP': row})
        with self.assertRaisesRegex(ValueError, 'UDP'):
            upnp.renew(gateway, '192.168.86.100', 30814, 604800)
        self.assertEqual(gateway.reads, ['TCP', 'UDP'])
        self.assertEqual(gateway.writes, [])

    def test_another_application_on_same_host_is_untouched(self):
        row = mapping()
        row['NewPortMappingDescription'] = 'existing application'
        gateway = FakeGateway({'TCP': row})
        with self.assertRaises(ValueError):
            upnp.renew(gateway, '192.168.86.100', 30814, 604800)
        self.assertEqual(gateway.writes, [])

    def test_creation_and_renewal_target_only_game_port(self):
        gateway = FakeGateway({'UDP': mapping()})
        result = upnp.renew(gateway, '192.168.86.100', 30814, 604800)
        self.assertEqual(gateway.reads[:2], ['TCP', 'UDP'])
        self.assertEqual({row['NewProtocol'] for row in gateway.writes}, {'TCP', 'UDP'})
        for row in gateway.writes:
            self.assertEqual((row['NewExternalPort'], row['NewInternalPort']), (30814, 30814))
            self.assertEqual(row['NewInternalClient'], '192.168.86.100')
            self.assertEqual(row['NewLeaseDuration'], 604800)
        self.assertEqual(result['mappings']['TCP']['lease_seconds'], 604800)

    def test_check_mode_never_renews(self):
        gateway = FakeGateway({'TCP': mapping(), 'UDP': mapping()})
        upnp.renew(gateway, '192.168.86.100', 30814, 604800, check=True)
        self.assertEqual(gateway.writes, [])

    def test_only_defined_missing_mapping_error_is_accepted(self):
        gateway = object.__new__(upnp.Gateway)
        for code in ('714', '401', '501'):
            def fail(*args):
                raise upnp.IGDError(code, 'failure')
            gateway.call = fail
            if code == '714':
                self.assertIsNone(gateway.mapping(30814, 'TCP'))
            else:
                with self.assertRaises(upnp.IGDError):
                    gateway.mapping(30814, 'TCP')

    def test_empty_remote_host_is_compatible_with_deployed_igd(self):
        gateway = object.__new__(upnp.Gateway)
        gateway.service, gateway.control = 'urn:schemas-upnp-org:service:WANIPConnection:2', 'http://192.168.86.1:5000/ctl/IPConn'
        with patch.object(upnp.urllib.request, 'urlopen', return_value=io.BytesIO(b'<response/>')) as send:
            gateway.call('GetSpecificPortMappingEntry', {'NewRemoteHost': '', 'NewExternalPort': 30814, 'NewProtocol': 'TCP'})
        self.assertIn(b'<NewRemoteHost></NewRemoteHost>', send.call_args.args[0].data)

    def test_router_that_ignores_renewal_refreshes_only_own_expiring_mapping(self):
        class StubbornGateway(FakeGateway):
            def call(self, action, arguments):
                protocol = arguments.get('NewProtocol')
                if action == 'DeletePortMapping':
                    self.writes.append(dict(action=action, **arguments))
                    del self.existing[protocol]
                    return {}
                if action == 'AddPortMapping' and protocol in self.existing:
                    return {}  # Router quirk: success but old expiry remains.
                return super().call(action, arguments)
        soon, permanent = mapping(), mapping()
        soon['NewLeaseDuration'], permanent['NewLeaseDuration'] = '1800', '0'
        gateway = StubbornGateway({'TCP': soon, 'UDP': permanent})
        result = upnp.renew(gateway, '192.168.86.100', 30814, 604800)
        deletions = [row for row in gateway.writes if row.get('action') == 'DeletePortMapping']
        self.assertEqual(len(deletions), 1)
        self.assertEqual(deletions[0]['NewProtocol'], 'TCP')
        self.assertTrue(result['mappings']['TCP']['recreated'])
        self.assertEqual(result['mappings']['TCP']['lease_seconds'], 604800)
        self.assertEqual(result['mappings']['UDP']['lease_seconds'], 0)

    def test_changed_owner_before_expiry_refresh_is_not_deleted(self):
        class ChangedGateway(FakeGateway):
            def call(self, action, arguments):
                result = super().call(action, arguments)
                if action == 'AddPortMapping':
                    row = self.existing[arguments['NewProtocol']]
                    row['NewInternalClient'], row['NewLeaseDuration'] = '192.168.86.200', '1800'
                return result
        gateway = ChangedGateway({'TCP': mapping(), 'UDP': mapping()})
        with self.assertRaisesRegex(ValueError, 'owner changed'):
            upnp.renew(gateway, '192.168.86.100', 30814, 604800)

    def test_transient_add_failure_after_delete_is_recovered_immediately(self):
        gateway = ExpiringGateway(failures=1)
        with patch.object(upnp.time, 'sleep'):
            result = upnp.renew(gateway, '192.168.86.100', 30814, 604800)
        self.assertEqual((gateway.deletions, gateway.restores), (1, 2))
        self.assertTrue(result['mappings']['TCP']['recreated'])
        self.assertEqual(result['mappings']['TCP']['lease_seconds'], 604800)
        self.assertEqual(result['mappings']['UDP']['lease_seconds'], 0)

    def test_foreign_owner_appearing_during_retry_is_not_overwritten(self):
        gateway = ExpiringGateway(failures=1, foreign_on_retry=True)
        with patch.object(upnp.time, 'sleep'):
            with self.assertRaisesRegex(ValueError, 'owner changed during restore'):
                upnp.renew(gateway, '192.168.86.100', 30814, 604800)
        self.assertEqual((gateway.deletions, gateway.restores), (1, 1))
        self.assertEqual(gateway.existing['TCP']['NewInternalClient'], '192.168.86.200')

    def test_lost_success_reply_is_read_back_without_duplicate_add(self):
        gateway = ExpiringGateway(lost_success_reply=True)
        result = upnp.renew(gateway, '192.168.86.100', 30814, 604800)
        self.assertEqual((gateway.deletions, gateway.restores), (1, 1))
        self.assertEqual(result['mappings']['TCP']['lease_seconds'], 604800)

    def test_recreation_retries_are_bounded(self):
        gateway = ExpiringGateway(failures=100)
        with patch.object(upnp.time, 'sleep') as delay:
            with self.assertRaisesRegex(RuntimeError, 'after 3 attempts'):
                upnp.renew(gateway, '192.168.86.100', 30814, 604800)
        self.assertEqual((gateway.deletions, gateway.restores), (1, 3))
        self.assertEqual([call.args[0] for call in delay.call_args_list], [1, 2])
        self.assertEqual(gateway.existing['UDP']['NewLeaseDuration'], '0')

    def test_foreign_description_after_add_is_rejected(self):
        class ChangedDescription(FakeGateway):
            def call(self, action, arguments):
                result = super().call(action, arguments)
                if action == 'AddPortMapping':
                    self.existing[arguments['NewProtocol']]['NewPortMappingDescription'] = 'another application'
                return result
        gateway = ChangedDescription()
        with self.assertRaisesRegex(ValueError, 'owner changed after renewal'):
            upnp.renew(gateway, '192.168.86.100', 30814, 604800)
        self.assertEqual(len(gateway.writes), 1, 'stop before any further writes')

    def test_final_verification_checks_application_description(self):
        class FinalDescriptionChange(FakeGateway):
            def call(self, action, arguments):
                result = super().call(action, arguments)
                if action == 'GetExternalIPAddress':
                    self.existing['TCP']['NewPortMappingDescription'] = 'another application'
                return result
        gateway = FinalDescriptionChange()
        with self.assertRaisesRegex(ValueError, 'missing or different after operation'):
            upnp.renew(gateway, '192.168.86.100', 30814, 604800)

    def test_lost_successful_delete_reply_restores_without_second_delete(self):
        class LostDeleteReply(ExpiringGateway):
            def call(self, action, arguments):
                result = super().call(action, arguments)
                if action == 'DeletePortMapping':
                    raise TimeoutError('router deleted mapping but response was lost')
                return result
        gateway = LostDeleteReply()
        result = upnp.renew(gateway, '192.168.86.100', 30814, 604800)
        self.assertEqual((gateway.deletions, gateway.restores), (1, 1))
        self.assertTrue(result['mappings']['TCP']['recreated'])
        self.assertEqual(result['mappings']['TCP']['lease_seconds'], 604800)

    def test_failed_delete_with_mapping_still_present_is_left_available(self):
        class FailedDelete(ExpiringGateway):
            def call(self, action, arguments):
                if action == 'DeletePortMapping':
                    self.deletions += 1
                    raise OSError('router did not process the delete')
                return super().call(action, arguments)
        gateway = FailedDelete()
        result = upnp.renew(gateway, '192.168.86.100', 30814, 604800)
        self.assertEqual((gateway.deletions, gateway.restores), (1, 0))
        self.assertFalse(result['mappings']['TCP']['recreated'])
        self.assertEqual(result['mappings']['TCP']['lease_seconds'], 1800)


if __name__ == '__main__':
    unittest.main()
