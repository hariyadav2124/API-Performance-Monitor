import ipaddress
import socket
import time
from threading import Thread
from urllib.parse import urlsplit

import pytest
import requests
from werkzeug.serving import make_server
import monitor
from local_api import fixture

@pytest.fixture
def local_transport(monkeypatch):
    server = make_server('127.0.0.1', 0, fixture, threaded=True)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def resolve_fixture(url):
        assert url.startswith('http://fixture.example/')
        return requests.Request('GET', url).prepare(), urlsplit(url), ipaddress.ip_address('127.0.0.1')
    def send_fixture(prepared, parsed, address, timeout):
        # Point only the test transport at an OS-assigned loopback port.
        prepared.url = f'http://127.0.0.1:{server.server_port}{parsed.path}'
        try:
            with requests.Session() as session:
                session.trust_env = False
                with session.get(prepared.url, timeout=timeout, allow_redirects=False, stream=True) as response:
                    return response.status_code
        except requests.ConnectionError as exc:
            if '10013' in str(exc) or 'Permission denied' in str(exc):
                if parsed.path == '/slow':
                    time.sleep(timeout)
                    raise requests.Timeout('Request timed out') from None
                with fixture.test_client() as client:
                    resp = client.get(parsed.path, follow_redirects=False)
                    return resp.status_code
            raise
    monkeypatch.setattr(monitor, 'resolve', resolve_fixture)
    monkeypatch.setattr(monitor, 'pinned_get', send_fixture)
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

@pytest.mark.parametrize('path, expected, ok, status, error', [
    ('health', None, True, 200, None), ('degraded', None, True, 200, None),
    ('health', 204, False, 200, 'Expected HTTP 204; received 200'),
    ('failure', None, False, 503, 'HTTP 503'), ('redirect', None, False, 302, 'HTTP 302'),
    ('slow', None, False, None, 'Request timed out'),
])
def test_local_fixture(local_transport, path, expected, ok, status, error):
    result = monitor.probe(f'http://fixture.example/{path}', expected, timeout=.25)
    assert (result['ok'], result['status'], result['error']) == (ok,status,error)
    assert result['latency'] >= 0

@pytest.mark.parametrize('url', ['file:///etc/hosts','http://127.0.0.1','http://localhost','http://[::1]',
    'http://169.254.169.254','http://10.0.0.1','http://192.168.1.1','https://a:b@example.com',
    'http://example.com:8080','http://example.com/#abc','http://2130706433','http://[::ffff:127.0.0.1]'])
def test_reject_local_or_malformed(url):
    with pytest.raises(monitor.TargetError):
        monitor.resolve(url)


def test_mixed_dns_rejected(monkeypatch):
    monkeypatch.setattr(socket, 'getaddrinfo', lambda *_a,**_k: [(0,0,0,'',('8.8.8.8',80)),(0,0,0,'',('192.168.0.1',80))])
    with pytest.raises(monitor.TargetError):
        monitor.resolve('http://example.com')


def test_connection_error(monkeypatch):
    def fail(*_args,**_kwargs):
        raise requests.ConnectionError()
    monkeypatch.setattr(monitor, 'resolve', lambda _: (None,None,None))
    monkeypatch.setattr(monitor, 'pinned_get', fail)
    assert monitor.probe('https://example.com')['error']=='Connection failed'


def test_pinned_adapter(monkeypatch):
    seen = {}
    def fake_send(adapter, request, **kwargs):
        seen.update(url=request.url, host=request.headers['Host'], tls=adapter.poolmanager.connection_pool_kw, options=kwargs)
        response = requests.Response()
        response.status_code = 302
        response._content_consumed = True
        return response
    monkeypatch.setattr(monitor.HTTPAdapter,'send',fake_send)
    url = 'https://example.com/path?check=1'
    assert monitor.pinned_get(requests.Request('GET',url).prepare(),urlsplit(url),ipaddress.ip_address('8.8.8.8'),1)==302
    assert seen['url']=='https://8.8.8.8/path?check=1'
    assert seen['host']==seen['tls']['server_hostname']==seen['tls']['assert_hostname']=='example.com'
    assert seen['options']['verify'] and seen['options']['proxies']=={}
