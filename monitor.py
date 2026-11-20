"""Public-only HTTP probes; DNS answers are validated and the socket is pinned."""
import ipaddress
import logging
import os
import socket
import time
from urllib.parse import urlsplit

import requests
from requests.adapters import HTTPAdapter
from database import db, is_supabase_enabled, record_supabase

try:
    TIMEOUT = float(os.getenv('MONITOR_TIMEOUT') or 5)
except ValueError:
    raise ValueError('MONITOR_TIMEOUT must be a number of seconds, for example: set MONITOR_TIMEOUT=5') from None
if not 0 < TIMEOUT <= 60:
    raise ValueError(f'MONITOR_TIMEOUT must be between 0 and 60 seconds (got {TIMEOUT}).')
LOG = logging.getLogger(__name__)

class TargetError(ValueError):
    pass


def resolve(url):
    try:
        if not url or len(url) > 2048 or any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url) or '\\' in url:
            raise ValueError()
        raw = urlsplit(url)
        if raw.scheme not in ('http', 'https') or raw.username is not None or raw.password is not None or '#' in url or not raw.hostname or '%' in raw.netloc:
            raise ValueError()
        prepared = requests.Request('GET', url).prepare()
        parsed = urlsplit(prepared.url)
        port = 443 if parsed.scheme == 'https' else 80
        if raw.port is not None and raw.port != port:
            raise ValueError()
    except (ValueError, requests.RequestException, UnicodeError) as exc:
        raise TargetError('Use a valid public HTTP(S) URL on its default port, without credentials or fragments.') from exc
    host = parsed.hostname
    if host.rstrip('.').lower().endswith(('.localhost', '.local', '.internal', '.home.arpa')) or '.' not in host and ':' not in host:
        raise TargetError('Only public Internet hosts are allowed.')
    try:
        addresses = list(dict.fromkeys(ipaddress.ip_address(r[4][0]) for r in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)))
    except (OSError, ValueError) as exc:
        raise TargetError('DNS lookup failed.') from exc
    for address in addresses:
        if (not address.is_global or address.is_multicast or address.is_reserved or address.is_loopback or
                isinstance(address, ipaddress.IPv6Address) and (address.ipv4_mapped or address.sixtofour or address.teredo or address in ipaddress.ip_network('64:ff9b::/96'))):
            raise TargetError('Only public Internet addresses are allowed.')
    if not addresses:
        raise TargetError('DNS returned no addresses.')
    return prepared, parsed, min(addresses, key=lambda ip: ip.version)


def pinned_get(prepared, parsed, address, timeout):
    authority = f'[{address}]' if address.version == 6 else str(address)
    prepared.url = parsed._replace(netloc=authority).geturl()
    prepared.headers['Host'] = parsed.netloc
    prepared.headers['User-Agent'] = 'API-Sentinel/1.0'
    adapter = HTTPAdapter(max_retries=0)
    if parsed.scheme == 'https':
        adapter.poolmanager.connection_pool_kw.update(server_hostname=parsed.hostname, assert_hostname=parsed.hostname)
    try:
        # Send directly: no environment proxies, redirects, cookies or body downloads.
        with adapter.send(prepared, stream=True, timeout=(timeout, timeout), verify=True, proxies={}) as response:
            return response.status_code
    finally:
        adapter.close()


def probe(url, expected_status=None, timeout=TIMEOUT):
    started = time.perf_counter()
    status, error = None, None
    try:
        prepared, parsed, address = resolve(url)
        status = pinned_get(prepared, parsed, address, timeout)
        if expected_status is not None and status != expected_status:
            error = f'Expected HTTP {expected_status}; received {status}'
        elif not 200 <= status < 300:
            error = f'HTTP {status}'
    except TargetError as exc:
        error = str(exc)
    except requests.Timeout:
        error = 'Request timed out'
    except requests.exceptions.SSLError:
        error = 'TLS verification or handshake failed'
    except requests.ConnectionError:
        error = 'Connection failed'
    except requests.RequestException:
        error = 'HTTP request failed'
    except Exception:
        LOG.exception('Unexpected probe error')
        error = 'Unexpected probe error'
    return dict(status=status, error=error, ok=error is None, latency=round((time.perf_counter()-started)*1000, 1))


def record(endpoint, source):
    ts = time.time()
    result = probe(endpoint['url'], endpoint['expected_status'])
    if is_supabase_enabled():
        res = record_supabase(endpoint, result, source, ts)
        if res and not result['ok']:
            LOG.warning('Endpoint %s failed: %s', endpoint['id'], result['error'])
        return res
    with db() as conn:
        inserted = conn.execute('''INSERT INTO checks(endpoint_id,ts,latency,status,ok,error,source)
            SELECT id,?,?,?,?,?,? FROM endpoints WHERE id=? AND revision=?''',
            (ts, result['latency'], result['status'], result['ok'], result['error'], source, endpoint['id'], endpoint['revision'])).rowcount
    if inserted and not result['ok']:
        LOG.warning('Endpoint %s failed: %s', endpoint['id'], result['error'])
    return result if inserted else None
