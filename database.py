"""Storage layer supporting Supabase (cloud PostgreSQL via REST API) and local SQLite."""
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
import requests

LOG = logging.getLogger(__name__)

SUPABASE_URL = os.getenv('SUPABASE_URL', 'https://tpewofwnqlweezgvuqjl.supabase.co').rstrip('/')
SUPABASE_KEY = os.getenv('SUPABASE_KEY', 'sb_publishable_8ALvZxIQR_Xg_betBmsW0g_4wcNxvrA')
MONITOR_DB_BACKEND = os.getenv('MONITOR_DB_BACKEND', 'auto')

PATH = Path(os.getenv('MONITOR_DB', Path(__file__).parent / 'instance' / 'monitor.sqlite3'))

# Global requests session with connection pooling for Supabase REST API
_http = requests.Session()
_http.headers.update({
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json'
})

_supabase_ready = None


def is_supabase_enabled():
    """Return True if Supabase is configured and tables exist; False for local SQLite."""
    global _supabase_ready
    if MONITOR_DB_BACKEND == 'sqlite':
        return False
    # If a custom test DB path is set in env (e.g. during pytest), use SQLite
    env_db = os.getenv('MONITOR_DB', '')
    if env_db and 'instance' not in env_db and not env_db.endswith('monitor.sqlite3'):
        return False
    if not (SUPABASE_URL and SUPABASE_KEY):
        return False
    if _supabase_ready is not None:
        return _supabase_ready
    try:
        r = _http.get(
            f'{SUPABASE_URL}/rest/v1/endpoints?select=count',
            headers={'Prefer': 'count=exact'},
            timeout=4
        )
        if r.status_code in (200, 206):
            _supabase_ready = True
            return True
        _supabase_ready = False
        return False
    except Exception:
        _supabase_ready = False
        return False


def set_supabase_ready(val):
    global _supabase_ready
    _supabase_ready = val


@contextmanager
def db():
    """SQLite context manager (kept for local fallback and test compatibility)."""
    conn = sqlite3.connect(PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def init():
    """Ensure local SQLite tables exist. Supabase tables are created via supabase_schema.sql."""
    PATH.parent.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS endpoints (
          id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, url TEXT NOT NULL,
          description TEXT NOT NULL, interval INTEGER NOT NULL, threshold INTEGER NOT NULL,
          expected_status INTEGER, next_due REAL NOT NULL DEFAULT 0,
          revision INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS checks (
          id INTEGER PRIMARY KEY, endpoint_id INTEGER NOT NULL REFERENCES endpoints(id) ON DELETE CASCADE,
          ts REAL NOT NULL, latency REAL NOT NULL, status INTEGER,
          ok INTEGER NOT NULL, error TEXT, source TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS checks_endpoint_ts ON checks(endpoint_id, ts DESC);
        CREATE INDEX IF NOT EXISTS checks_ts ON checks(ts DESC);
        ''')


# ====================================================================
# Supabase CRUD & Helper Methods
# ====================================================================

def get_endpoints_supabase():
    r = _http.get(f'{SUPABASE_URL}/rest/v1/endpoints?order=id.asc', timeout=10)
    r.raise_for_status()
    return r.json()


def get_endpoint_supabase(endpoint_id):
    r = _http.get(f'{SUPABASE_URL}/rest/v1/endpoints?id=eq.{endpoint_id}&limit=1', timeout=10)
    r.raise_for_status()
    items = r.json()
    return items[0] if items else None


def add_endpoint_supabase(values):
    # values: (name, url, description, interval, threshold, expected_status)
    r_count = _http.get(
        f'{SUPABASE_URL}/rest/v1/endpoints?select=count',
        headers={'Prefer': 'count=exact'},
        timeout=10
    )
    count = int(r_count.headers.get('content-range', '0/0').split('/')[-1])
    if count >= 50:
        from flask import abort
        abort(409, 'The 50-monitor limit has been reached.')
    payload = {
        'name': values[0],
        'url': values[1],
        'description': values[2],
        'interval': values[3],
        'threshold': values[4],
        'expected_status': values[5]
    }
    r = _http.post(
        f'{SUPABASE_URL}/rest/v1/endpoints',
        headers={'Prefer': 'return=representation'},
        json=payload,
        timeout=10
    )
    r.raise_for_status()
    return r.json()[0]['id']


def update_endpoint_supabase(endpoint_id, values):
    old = get_endpoint_supabase(endpoint_id)
    if not old:
        return False
    if old['url'] != values[1] or old.get('expected_status') != values[5]:
        _http.delete(f'{SUPABASE_URL}/rest/v1/checks?endpoint_id=eq.{endpoint_id}', timeout=10)
    payload = {
        'name': values[0],
        'url': values[1],
        'description': values[2],
        'interval': values[3],
        'threshold': values[4],
        'expected_status': values[5],
        'revision': old.get('revision', 0) + 1,
        'next_due': 0.0
    }
    r = _http.patch(f'{SUPABASE_URL}/rest/v1/endpoints?id=eq.{endpoint_id}', json=payload, timeout=10)
    return r.status_code in (200, 204)


def delete_endpoint_supabase(endpoint_id):
    r = _http.delete(
        f'{SUPABASE_URL}/rest/v1/endpoints?id=eq.{endpoint_id}',
        headers={'Prefer': 'return=representation'},
        timeout=10
    )
    return bool(r.status_code == 200 and r.json())


def record_supabase(endpoint, result, source, ts):
    # Optimistic lock: verify endpoint revision hasn't changed
    r_rev = _http.get(
        f'{SUPABASE_URL}/rest/v1/endpoints?id=eq.{endpoint["id"]}&select=revision',
        timeout=10
    )
    items = r_rev.json() if r_rev.status_code == 200 else []
    if not items or items[0].get('revision') != endpoint.get('revision'):
        return None
    payload = {
        'endpoint_id': endpoint['id'],
        'ts': ts,
        'latency': result['latency'],
        'status': result['status'],
        'ok': int(result['ok']),
        'error': result['error'],
        'source': source
    }
    _http.post(f'{SUPABASE_URL}/rest/v1/checks', json=payload, timeout=10)
    return result


def get_due_endpoints_supabase(now, active_keys, max_pool=8):
    r = _http.get(
        f'{SUPABASE_URL}/rest/v1/endpoints?next_due=lte.{now}&order=next_due.asc&limit={max_pool}',
        timeout=10
    )
    if r.status_code != 200:
        return []
    rows = r.json()
    queued = []
    for row in rows:
        if len(active_keys) + len(queued) >= max_pool:
            break
        if row['id'] not in active_keys:
            # Atomic update on Supabase
            patch_url = f'{SUPABASE_URL}/rest/v1/endpoints?id=eq.{row["id"]}&revision=eq.{row.get("revision", 0)}&next_due=lte.{now}'
            payload = {'next_due': now + row['interval']}
            r_up = _http.patch(
                patch_url,
                headers={'Prefer': 'return=representation'},
                json=payload,
                timeout=5
            )
            if r_up.status_code == 200 and r_up.json():
                queued.append(row)
    return queued


def seed_public_supabase(demo_list, resolve_fn):
    count = 0
    for name, url, desc in demo_list:
        try:
            resolve_fn(url)
        except Exception:
            continue
        # Check if already exists
        r_ex = _http.get(f'{SUPABASE_URL}/rest/v1/endpoints?url=eq.{url}&limit=1', timeout=10)
        if r_ex.status_code == 200 and r_ex.json():
            continue
        _http.post(f'{SUPABASE_URL}/rest/v1/endpoints', json={
            'name': name,
            'url': url,
            'description': desc,
            'interval': 60,
            'threshold': 500
        }, timeout=10)
        count += 1
    return count
