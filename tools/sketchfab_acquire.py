"""Developer-only bounded Sketchfab acquisition. Never persist temporary URLs or credentials."""
import argparse
from contextlib import nullcontext
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import time
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'server'))
import assetcatalog as ac
import compiledcache
import netfetch
import staging

API = 'https://api.sketchfab.com/v3/models/'
ARCHIVE_HOSTS = frozenset({'sketchfab-prod-media.s3.amazonaws.com',
                           'sketchfab-prod-media.s3.eu-west-1.amazonaws.com', 'media.sketchfab.com'})


class AcquisitionError(ValueError):
    pass


class DownloadExpired(AcquisitionError):
    pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _url(url, api=False):
    parsed = urlsplit(url)
    allowed = parsed.hostname == 'api.sketchfab.com' if api else parsed.hostname in ARCHIVE_HOSTS
    if parsed.scheme != 'https' or not allowed or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise AcquisitionError('provider URL host or scheme is not allowed')
    return url


def authorization(mode, environ=None):
    """Explicit modes only. A Data API token is never reinterpreted as OAuth."""
    environ = os.environ if environ is None else environ
    name, prefix = {'api-token': ('SKETCHFAB_API_TOKEN', 'Token'),
                    'oauth': ('SKETCHFAB_OAUTH_TOKEN', 'Bearer')}.get(mode, (None, None))
    token = environ.get(name, '') if name else ''
    if not token or token != token.strip() or any(c in token for c in '\r\n'):
        raise AcquisitionError('the explicitly selected developer credential is unavailable')
    return {'Authorization': prefix + ' ' + token}


def _request(url, headers, fetcher, opener, timeout, api=False):
    _url(url, api=api)
    # Session admission/counters reuse the companion's one network boundary.
    return fetcher.session(url), Request(url, headers=dict({'User-Agent': netfetch.USER_AGENT}, **headers))


def download_descriptor(uid, mode, fetcher, opener=None, environ=None):
    if not ac.UID.fullmatch(uid):
        raise AcquisitionError('invalid model UID')
    opener = opener or build_opener(NoRedirect())
    headers = authorization(mode, environ)
    url = API + uid + '/download'
    context, request = _request(url, headers, fetcher, opener, 30, api=True)
    try:
        with context as session, opener.open(request, timeout=30) as response:
            session.status = response.status
            if response.status != 200:
                raise AcquisitionError('download authorization did not return HTTP 200')
            body = response.read((1 << 20) + 1)
            session.nbytes = len(body)
            if len(body) > 1 << 20:
                raise AcquisitionError('download descriptor exceeded metadata budget')
            payload = json.loads(body)
            if not isinstance(payload, dict) or not isinstance(payload.get('gltf'), dict):
                raise AcquisitionError('download descriptor is malformed')
            record = payload['gltf']
    except HTTPError as error:
        raise AcquisitionError(f'download authorization failed with HTTP {error.code}') from None
    except (OSError, json.JSONDecodeError):
        raise AcquisitionError('download authorization transport or JSON failed') from None
    finally:
        headers.clear()
    if (type(record.get('size')) is not int or record['size'] <= 0
            or type(record.get('expires')) not in (int, float) or not 0 < record['expires'] <= 3600):
        raise AcquisitionError('download descriptor is incomplete')
    _url(record.get('url', ''))
    # This object is transient in memory; never return it from the CLI or serialize it.
    return {'url': record['url'], 'size': record['size'], 'deadline': time.monotonic() + record['expires']}


def verify_public_metadata(manifest, fetcher, opener=None):
    """Refresh public eligibility immediately before requesting authenticated archive access."""
    opener = opener or build_opener(NoRedirect())
    uid = manifest['source']['model_uid']
    url = API + uid
    context, request = _request(url, {}, fetcher, opener, 30, api=True)
    try:
        with context as session, opener.open(request, timeout=30) as response:
            session.status = response.status
            if response.status != 200:
                raise AcquisitionError('public model metadata unavailable')
            body = response.read((1 << 20) + 1)
            session.nbytes = len(body)
            if len(body) > 1 << 20:
                raise AcquisitionError('model metadata exceeded budget')
            record = json.loads(body)
            if not isinstance(record, dict):
                raise AcquisitionError('public model metadata is malformed')
    except (HTTPError, OSError, json.JSONDecodeError):
        raise AcquisitionError('public model metadata refresh failed') from None
    license = record.get('license')
    if not isinstance(license, dict):
        raise AcquisitionError('public model license metadata is malformed')
    raw_license_url = license.get('url')
    if not isinstance(raw_license_url, str):
        raise AcquisitionError('public model license metadata is malformed')
    license_url = raw_license_url.replace('http://creativecommons.org/', 'https://creativecommons.org/')
    if (record.get('uid') != uid or record.get('isDownloadable') is not True
            or record.get('name') != manifest['source']['model_name']
            or license_url != manifest['license']['url']):
        raise AcquisitionError('current model UID/downloadability/license does not match approved evidence')
    creator = record.get('user') or {}
    if not isinstance(creator, dict):
        raise AcquisitionError('public model creator metadata is malformed')
    if (creator.get('profileUrl') != manifest['source']['creator_url']
            or creator.get('displayName') != manifest['source']['creator_name']):
        raise AcquisitionError('current model creator does not match approved evidence')
    return {'uid': uid, 'license_url': license_url, 'is_downloadable': True,
            'metadata_sha256': hashlib.sha256(body).hexdigest(), 'verified_at': ac.utcnow()}


class BatchBudget:
    """Serial persistent actual-body-byte ledger, including interrupted downloads."""
    def __init__(self, root, limits):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'acquisition-ledger.json'
        self.limits = limits
        self.state = {'schema_version': 1, 'actual_download_bytes': 0, 'models': []}
        if self.path.exists():
            self.state = json.loads(self.path.read_text(encoding='utf-8'))
        if (not isinstance(self.state, dict) or type(self.state.get('schema_version')) is not int
                or self.state.get('schema_version') != 1 or type(self.state.get('actual_download_bytes')) is not int
                or self.state['actual_download_bytes'] < 0 or not isinstance(self.state.get('models'), list)
                or any(not isinstance(uid, str) or not ac.UID.fullmatch(uid) for uid in self.state['models'])):
            raise AcquisitionError('invalid acquisition byte ledger')

    def admit(self, uid):
        models = self.state['models']
        if uid not in models:
            if len(models) >= self.limits['candidate_limit']:
                raise AcquisitionError('prototype candidate limit reached')
            models.append(uid)
        ac.atomic_json(self.path, self.state)

    def remaining(self):
        return self.limits['batch_download_bytes'] - self.state['actual_download_bytes']

    def count(self, size):
        if size > self.remaining():
            raise AcquisitionError('prototype actual download byte limit reached')
        self.state['actual_download_bytes'] += size
        ac.atomic_json(self.path, self.state)


def download_archive(uid, descriptor, root, limits, fetcher, opener=None, expected_sha256=None, budget=None):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    if (not ac.UID.fullmatch(uid) or not isinstance(descriptor, dict)
            or type(descriptor.get('size')) is not int or descriptor['size'] <= 0
            or type(descriptor.get('deadline')) not in (int, float)
            or descriptor['size'] > limits['archive_bytes_per_asset']):
        raise AcquisitionError('model archive exceeds prototype budget')
    if expected_sha256 is not None and not ac.SHA.fullmatch(expected_sha256):
        raise AcquisitionError('invalid expected source checksum')
    opener = opener or build_opener(NoRedirect())
    budget = budget or BatchBudget(root, limits)
    partial, metadata_path = root / (uid + '.part'), root / (uid + '.resume.json')
    budget.admit(uid)
    previous = json.loads(metadata_path.read_text(encoding='utf-8')) if metadata_path.exists() else {}
    offset = partial.stat().st_size if partial.exists() else 0
    if offset and (previous.get('uid') != uid or previous.get('expected_bytes') != descriptor['size'] or not previous.get('etag')
                   or previous.get('partial_sha256') != ac.sha256(partial)):
        raise AcquisitionError('partial archive identity/checksum cannot be resumed')
    if offset >= descriptor['size']:
        raise AcquisitionError('unexpected complete/oversized partial archive')
    headers = {}
    if offset:
        headers = {'Range': f'bytes={offset}-', 'If-Range': previous['etag']}
    url, redirects = descriptor['url'], 0
    while True:
        if time.monotonic() >= descriptor['deadline']:
            raise DownloadExpired('temporary archive authorization expired before transfer')
        context, request = _request(url, headers, fetcher, opener, limits['request_timeout_seconds'])
        try:
            with context as session:
                try:
                    response = opener.open(request, timeout=limits['request_timeout_seconds'])
                except HTTPError as error:
                    session.status = error.code
                    if error.code in (301, 302, 303, 307, 308):
                        redirects += 1
                        if redirects > limits['redirect_limit']:
                            raise AcquisitionError('archive redirect limit exceeded')
                        url = _url(urljoin(url, error.headers.get('Location', '')))
                        continue
                    if error.code in (401, 403, 410):
                        raise DownloadExpired(f'archive authorization expired or denied (HTTP {error.code})') from None
                    raise AcquisitionError(f'archive transfer failed with HTTP {error.code}') from None
                with response:
                    session.status = response.status
                    expected_status = 206 if offset else 200
                    if response.status != expected_status:
                        raise AcquisitionError('archive range/status mismatch; original partial preserved')
                    etag = response.headers.get('ETag')
                    if offset:
                        wanted = f'bytes {offset}-{descriptor["size"]-1}/{descriptor["size"]}'
                        if response.headers.get('Content-Range') != wanted or etag != previous['etag']:
                            raise AcquisitionError('archive resume version/range mismatch')
                    length = response.headers.get('Content-Length')
                    if length is not None and int(length) != descriptor['size'] - offset:
                        raise AcquisitionError('archive declared length mismatch')
                    state = {'uid': uid, 'etag': etag, 'expected_bytes': descriptor['size']}
                    start = time.monotonic()
                    try:
                        with partial.open('ab' if offset else 'xb') as output:
                            while True:
                                if time.monotonic() - start > limits['total_deadline_seconds']:
                                    raise AcquisitionError('archive transfer deadline exceeded')
                                remaining = descriptor['size'] - output.tell()
                                allowance = min(1 << 20, remaining + 1, budget.remaining())
                                if allowance <= 0:
                                    raise AcquisitionError('prototype actual download byte limit reached')
                                chunk = response.read(allowance)
                                if not chunk:
                                    break
                                budget.count(len(chunk))
                                session.nbytes += len(chunk)
                                if len(chunk) > remaining:
                                    raise AcquisitionError('archive exceeded declared byte count')
                                output.write(chunk)
                    finally:
                        if partial.exists():
                            state['partial_sha256'] = ac.sha256(partial)
                            ac.atomic_json(metadata_path, state)
                    if partial.stat().st_size != descriptor['size']:
                        raise AcquisitionError('archive transfer interrupted; validated partial retained')
                    digest = ac.sha256(partial)
                    if expected_sha256 is not None and digest != expected_sha256:
                        raise AcquisitionError('archive source checksum mismatch')
                    target = root / (digest + '.zip')
                    if target.exists():
                        if ac.sha256(target) != digest:
                            raise AcquisitionError('existing original archive is corrupt')
                        partial.unlink()
                    else:
                        staging.replace_cached(str(partial), str(target))
                    return {'uid': uid, 'archive': str(target), 'sha256': digest, 'bytes': descriptor['size'],
                            'actual_batch_download_bytes': budget.state['actual_download_bytes'], 'downloaded_at': ac.utcnow()}
        except DownloadExpired:
            raise
        except (OSError, ValueError) as error:
            if isinstance(error, AcquisitionError):
                raise
            raise AcquisitionError('archive transfer or local integrity check failed') from None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--budgets', type=Path, required=True)
    parser.add_argument('--auth-mode', choices=('api-token', 'oauth'), required=True)
    args = parser.parse_args(argv)
    try:
        budgets = json.loads(args.budgets.read_text(encoding='utf-8'))
        manifest = json.loads(args.manifest.read_text(encoding='utf-8'))
        ac.validate_manifest(manifest, budgets=budgets)
        root = args.root.resolve()
        state = (Path(os.environ['LOCALAPPDATA']) / 'beamng-earth-2').resolve()
        if not root.is_relative_to(state):
            raise AcquisitionError('acquisition output must stay in dedicated state')
        uid = manifest['source']['model_uid']
        with compiledcache._guard(root.parent / '.acquisition.lock', timeout=1):
            fetcher = netfetch.Fetcher()
            try:
                evidence = verify_public_metadata(manifest, fetcher)
                ac.atomic_json(root / (uid + '.eligibility.json'), evidence)
                descriptor = download_descriptor(uid, args.auth_mode, fetcher)
                result = download_archive(uid, descriptor, root, budgets['acquisition'], fetcher)
            finally:
                fetcher.close()
        manifest['source'].update(downloaded_at=result['downloaded_at'], archive_sha256=result['sha256'], archive_bytes=result['bytes'])
        ac.atomic_json(root / (uid + '.manifest.json'), manifest)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (AcquisitionError, ValueError, OSError, KeyError):
        # Exception strings/tracebacks can contain temporary signed URLs; omit them.
        print(json.dumps({'status': 'rejected', 'reason': 'authorization, manifest, transfer or integrity validation failed'}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
