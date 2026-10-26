#!/usr/bin/env python3
"""Renew only Olympus's two BeamMP IGD mappings; Python standard library, no SSDP listener.

The router description URL was discovered on this LAN. Query both ports before
any write, and refuse to replace mappings owned by another client/application.
No BeamMP AuthKey, Podman socket, or other server port is used by this helper.
"""
import argparse
import ipaddress
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET


SOAP = 'http://schemas.xmlsoap.org/soap/envelope/'
DESCRIPTION = 'BeamMP Earth Olympus'
MAX_XML_BYTES = 128 * 1024
RECREATE_BELOW_SECONDS = 2 * 3600
RESTORE_ATTEMPTS = 3


class IGDError(RuntimeError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(f'IGD error {code}: {message}')


TRANSIENT_ERRORS = (OSError, IGDError, ET.ParseError)


def xml_response(response):
    data = response.read(MAX_XML_BYTES + 1)
    if len(data) > MAX_XML_BYTES:
        raise ValueError('Router response exceeds XML size limit')
    return ET.fromstring(data)


def values(xml):
    return {node.tag.rsplit('}', 1)[-1]: node.text or '' for node in xml.iter()}


class Gateway:
    def __init__(self, description_url):
        parsed = urllib.parse.urlsplit(description_url)
        if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password:
            raise ValueError('Expected an HTTP router description URL without credentials')
        if not ipaddress.ip_address(parsed.hostname).is_private:
            raise ValueError('Router must be a private LAN address')
        with urllib.request.urlopen(description_url, timeout=10) as response:
            document = xml_response(response)
        self.service = self.control = None
        for element in document.iter():
            if element.tag.rsplit('}', 1)[-1] != 'service':
                continue
            row = values(element)
            kind = row.get('serviceType', '')
            if ':service:WANIPConnection:' not in kind and ':service:WANPPPConnection:' not in kind:
                continue
            base = values(document).get('URLBase') or description_url
            control = urllib.parse.urljoin(base, row['controlURL'])
            target = urllib.parse.urlsplit(control)
            if target.scheme not in ('http', 'https') or target.hostname != parsed.hostname or target.username or target.password:
                raise ValueError('Router control URL must stay on the discovered LAN router')
            self.service, self.control = kind, control
            break
        if not self.control:
            raise ValueError('No IGD WAN connection service in router description')

    def call(self, action, arguments):
        envelope = ET.Element(ET.QName(SOAP, 'Envelope'))
        envelope.set(ET.QName(SOAP, 'encodingStyle'), 'http://schemas.xmlsoap.org/soap/encoding/')
        body = ET.SubElement(envelope, ET.QName(SOAP, 'Body'))
        method = ET.SubElement(body, ET.QName(self.service, action))
        for key, value in arguments.items():
            ET.SubElement(method, key).text = str(value)
        # The deployed MiniUPnPd 1.9 rejects self-closing empty NewRemoteHost tags.
        request = urllib.request.Request(self.control, data=ET.tostring(envelope, encoding='utf-8', xml_declaration=True,
                                                                        short_empty_elements=False),
                                         headers={'Content-Type': 'text/xml; charset="utf-8"',
                                                  'SOAPAction': f'"{self.service}#{action}"'}, method='POST')
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                result = values(xml_response(response))
        except urllib.error.HTTPError as error:
            result = values(xml_response(error))
            raise IGDError(result.get('errorCode', error.code), result.get('errorDescription', 'SOAP request failed')) from error
        if 'errorCode' in result:
            raise IGDError(result['errorCode'], result.get('errorDescription', 'SOAP request failed'))
        return result

    def mapping(self, port, protocol):
        try:
            return self.call('GetSpecificPortMappingEntry',
                             dict(NewRemoteHost='', NewExternalPort=port, NewProtocol=protocol))
        except IGDError as error:
            if str(error.code) == '714':  # NoSuchEntryInArray, the defined absent-mapping response
                return None
            raise


def owned(mapping, lan_ip, port):
    return (mapping is not None and mapping.get('NewInternalClient') == lan_ip
            and int(mapping.get('NewInternalPort', 0)) == port
            and mapping.get('NewPortMappingDescription') == DESCRIPTION)


def restore_mapping(gateway, arguments):
    """Recover a just-deleted mapping, including an Add whose success reply was lost.

    Each attempt reads ownership before adding and reads back afterward. A failed
    ownership read never permits a write; a foreign owner aborts without retries.
    """
    protocol, port, lan_ip = (arguments[key] for key in ('NewProtocol', 'NewExternalPort', 'NewInternalClient'))
    last_error = None
    wrote = False
    for attempt in range(RESTORE_ATTEMPTS):
        if attempt:
            time.sleep(attempt)  # at most three seconds total backoff
        try:
            current = gateway.mapping(port, protocol)
        except TRANSIENT_ERRORS as error:
            last_error = error
            continue
        if current is not None:
            if not owned(current, lan_ip, port):
                raise ValueError(f'{protocol} {port} owner changed during restore; unchanged')
            if current.get('NewEnabled') in ('1', 'true'):
                return current, wrote  # previous Add may have succeeded despite a lost response
        try:
            wrote = True
            gateway.call('AddPortMapping', arguments)
        except TRANSIENT_ERRORS as error:
            last_error = error
        try:
            current = gateway.mapping(port, protocol)
        except TRANSIENT_ERRORS as error:
            last_error = error
            continue
        if current is not None:
            if not owned(current, lan_ip, port):
                raise ValueError(f'{protocol} {port} owner changed during restore; unchanged')
            if current.get('NewEnabled') in ('1', 'true'):
                return current, wrote
        last_error = last_error or RuntimeError('mapping absent or disabled after AddPortMapping')
    raise RuntimeError(f'{protocol} {port} mapping could not be restored after {RESTORE_ATTEMPTS} attempts') from last_error


def renew(gateway, lan_ip, port, lease, check=False):
    existing = {protocol: gateway.mapping(port, protocol) for protocol in ('TCP', 'UDP')}
    # This entire preflight precedes writes, so a UDP conflict cannot leave a new TCP rule behind.
    for protocol, mapping in existing.items():
        if mapping is None:
            continue
        if not owned(mapping, lan_ip, port):
            raise ValueError(f'{protocol} {port} mapping is owned by another client/application; unchanged')
    recreated = set()
    if not check:
        for protocol in ('TCP', 'UDP'):
            arguments = dict(NewRemoteHost='', NewExternalPort=port, NewProtocol=protocol,
                             NewInternalPort=port, NewInternalClient=lan_ip, NewEnabled=1,
                             NewPortMappingDescription=DESCRIPTION, NewLeaseDuration=lease)
            current = gateway.mapping(port, protocol)
            if current is not None and not owned(current, lan_ip, port):
                raise ValueError(f'{protocol} {port} owner changed before renewal; unchanged')
            gateway.call('AddPortMapping', arguments)
            # This router accepts AddPortMapping but does not extend an existing lease.
            # Only near expiry, refresh our exact mapping after a fresh ownership check.
            # Do not delete permanent entries, unrelated entries, or healthy long leases.
            current = gateway.mapping(port, protocol)
            if current is not None and not owned(current, lan_ip, port):
                raise ValueError(f'{protocol} {port} owner changed after renewal; unchanged')
            remaining = int(current.get('NewLeaseDuration', 0)) if current else 0
            if 0 < remaining <= min(RECREATE_BELOW_SECONDS, lease // 2):
                if not owned(current, lan_ip, port):
                    raise ValueError(f'{protocol} {port} owner changed before refresh; unchanged')
                try:
                    gateway.call('DeletePortMapping', dict(NewRemoteHost='', NewExternalPort=port, NewProtocol=protocol))
                except TRANSIENT_ERRORS:
                    # The router may have deleted it before losing its response. Read and
                    # restore immediately; never issue a second delete on an uncertain result.
                    pass
                _, wrote = restore_mapping(gateway, arguments)
                if wrote:
                    recreated.add(protocol)
    result = {'external_ip': gateway.call('GetExternalIPAddress', {})['NewExternalIPAddress'], 'mappings': {}}
    for protocol in ('TCP', 'UDP'):
        mapping = gateway.mapping(port, protocol)
        if not owned(mapping, lan_ip, port):
            raise ValueError(f'{protocol} {port} mapping missing or different after operation')
        if mapping.get('NewEnabled') not in ('1', 'true'):
            raise ValueError(f'{protocol} {port} mapping is disabled')
        result['mappings'][protocol] = dict(external_port=port, internal_ip=lan_ip, internal_port=port,
                                           lease_seconds=int(mapping.get('NewLeaseDuration', 0)),
                                           recreated=protocol in recreated)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--router-url', default='http://192.168.86.1:5000/rootDesc.xml')
    parser.add_argument('--lan-ip', default='192.168.86.100')
    parser.add_argument('--port', type=int, default=30814)
    parser.add_argument('--lease', type=int, default=604800)
    parser.add_argument('--check', action='store_true', help='verify mappings without renewing them')
    args = parser.parse_args()
    if not ipaddress.ip_address(args.lan_ip).is_private or not 1024 < args.port < 65536 or not 60 <= args.lease <= 604800:
        parser.error('Expected private LAN IP, port 1025..65535, and lease 60..604800 seconds')
    try:
        print(json.dumps(renew(Gateway(args.router_url), args.lan_ip, args.port, args.lease, args.check), sort_keys=True))
    except Exception as error:
        print(f'BeamMP UPnP: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
