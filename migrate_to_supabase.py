"""Migrate data from local SQLite database (instance/monitor.sqlite3) to Supabase."""
import os
import sqlite3
import sys
import time
import requests

SUPABASE_URL = os.getenv('SUPABASE_URL', 'https://tpewofwnqlweezgvuqjl.supabase.co')
SUPABASE_KEY = os.getenv('SUPABASE_KEY', 'sb_publishable_8ALvZxIQR_Xg_betBmsW0g_4wcNxvrA')
SQLITE_DB = os.getenv('MONITOR_DB', os.path.join(os.path.dirname(__file__), 'instance', 'monitor.sqlite3'))

HEADERS = {
    'apikey': SUPABASE_KEY,
    'Authorization': f'Bearer {SUPABASE_KEY}',
    'Content-Type': 'application/json',
    'Prefer': 'return=representation'
}


def test_connection():
    try:
        r = requests.get(f'{SUPABASE_URL}/rest/v1/endpoints?select=count', headers=HEADERS, timeout=10)
        if r.status_code == 404:
            print('[ERROR] Supabase tables do not exist yet!')
            print('Please open your Supabase Dashboard -> SQL Editor, and run the contents of supabase_schema.sql first.')
            return False
        if r.status_code not in (200, 206):
            print(f'[ERROR] Supabase connection failed: HTTP {r.status_code} - {r.text}')
            return False
        return True
    except Exception as exc:
        print(f'[ERROR] Could not connect to Supabase: {exc}')
        return False


def migrate():
    print('=' * 66)
    print(' API Sentinel - Migrate SQLite -> Supabase')
    print('=' * 66)
    print(f' SQLite Database : {SQLITE_DB}')
    print(f' Supabase URL    : {SUPABASE_URL}')
    print('-' * 66)

    if not os.path.exists(SQLITE_DB):
        print(f'[ERROR] SQLite file not found: {SQLITE_DB}')
        return 1

    print('Checking Supabase connection and tables...')
    if not test_connection():
        return 1

    conn = sqlite3.connect(SQLITE_DB)
    conn.row_factory = sqlite3.Row

    # 1. Migrate endpoints
    endpoints = [dict(r) for r in conn.execute('SELECT * FROM endpoints ORDER BY id').fetchall()]
    print(f'Found {len(endpoints)} endpoint(s) in SQLite.')

    if endpoints:
        print(f'Uploading {len(endpoints)} endpoint(s) to Supabase...')
        r = requests.post(
            f'{SUPABASE_URL}/rest/v1/endpoints',
            headers={**HEADERS, 'Prefer': 'resolution=merge-duplicates'},
            json=endpoints,
            timeout=15
        )
        if r.status_code in (200, 201):
            print(f'[ OK ] {len(endpoints)} endpoint(s) uploaded successfully.')
        else:
            print(f'[WARN] Bulk insert failed (HTTP {r.status_code}): {r.text}. Retrying one by one...')
            for ep in endpoints:
                r_one = requests.post(
                    f'{SUPABASE_URL}/rest/v1/endpoints',
                    headers={**HEADERS, 'Prefer': 'resolution=merge-duplicates'},
                    json=ep,
                    timeout=10
                )
                if r_one.status_code in (200, 201):
                    print(f'  [ OK ] Endpoint #{ep["id"]} ({ep["name"]})')
                else:
                    print(f'  [FAIL] Endpoint #{ep["id"]} ({ep["name"]}): {r_one.text}')

    # 2. Migrate checks in batches
    checks = [dict(r) for r in conn.execute('SELECT * FROM checks ORDER BY id').fetchall()]
    print(f'\nFound {len(checks)} check record(s) in SQLite.')

    if checks:
        batch_size = 100
        total_batches = (len(checks) + batch_size - 1) // batch_size
        uploaded = 0
        print(f'Uploading {len(checks)} checks in {total_batches} batch(es)...')

        for i in range(0, len(checks), batch_size):
            batch = checks[i:i + batch_size]
            batch_num = (i // batch_size) + 1
            r = requests.post(
                f'{SUPABASE_URL}/rest/v1/checks',
                headers={**HEADERS, 'Prefer': 'resolution=merge-duplicates'},
                json=batch,
                timeout=20
            )
            if r.status_code in (200, 201):
                uploaded += len(batch)
                print(f'  [ OK ] Batch {batch_num}/{total_batches} ({uploaded}/{len(checks)} checks)')
            else:
                print(f'  [FAIL] Batch {batch_num}/{total_batches}: HTTP {r.status_code} - {r.text}')

    # 3. Verify counts in Supabase
    print('-' * 66)
    print('Verifying counts in Supabase...')
    r_ep = requests.get(f'{SUPABASE_URL}/rest/v1/endpoints?select=count', headers={**HEADERS, 'Prefer': 'count=exact'})
    ep_count = r_ep.headers.get('content-range', '').split('/')[-1] if r_ep.status_code in (200, 206) else '?'

    r_ch = requests.get(f'{SUPABASE_URL}/rest/v1/checks?select=count', headers={**HEADERS, 'Prefer': 'count=exact'})
    ch_count = r_ch.headers.get('content-range', '').split('/')[-1] if r_ch.status_code in (200, 206) else '?'

    print(f' Supabase Endpoints : {ep_count}')
    print(f' Supabase Checks    : {ch_count}')
    print('=' * 66)
    print(' Migration complete!')
    print('=' * 66)
    return 0


if __name__ == '__main__':
    sys.exit(migrate())
