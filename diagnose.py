"""Environment check for API Sentinel (Windows 11, Linux and macOS).

Run this first whenever the app will not start:

    python diagnose.py

It imports every dependency in a separate try/except, so a broken DLL is
reported with its exact message instead of crashing the whole app, and it
checks the database folder, the scheduler lock backend and the operator token.

Exit code 0 means everything needed is ready. It changes nothing and prints no
secrets; it never tries to work around an operating-system security policy.
"""
import importlib
import os
import platform
import sqlite3
import sys
from pathlib import Path

PACKAGES = [
    ('flask', 'Flask'),
    ('requests', 'requests'),
    ('urllib3', 'urllib3'),
    ('numpy', 'numpy'),
    ('pandas', 'pandas'),
    ('plotly', 'plotly'),
    ('apscheduler', 'APScheduler'),
]

CHECK, CROSS, WARN = '[ OK ]', '[FAIL]', '[WARN]'


def line(status, text):
    """ASCII-only output, safe for a Windows CMD code page."""
    print(f'{status} {text}', flush=True)


def version(name):
    try:
        from importlib.metadata import version as dist_version
        return dist_version({'apscheduler': 'APScheduler', 'flask': 'Flask'}.get(name, name))
    except Exception:
        return 'unknown'


def main():
    failures = 0
    print('=' * 66)
    print(' API Sentinel - environment check')
    print('-' * 66)
    line(CHECK, f'Python {platform.python_version()} on {platform.system()} {platform.release()} ({platform.machine()})')
    line(CHECK, f'Executable: {sys.executable}')
    if sys.prefix != getattr(sys, 'base_prefix', sys.prefix):
        line(CHECK, f'Virtual environment active: {sys.prefix}')
    else:
        line(WARN, 'No virtual environment active (expected if you use .venv + activate.bat)')
    if sys.version_info < (3, 11):
        failures += 1
        line(CROSS, 'Python 3.11 or newer is required.')

    print('-' * 66)
    for module_name, label in PACKAGES:
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:
            failures += 1
            line(CROSS, f'{label} could not be imported: {type(exc).__name__}: {exc}')
            if 'DLL load failed' in str(exc) or 'Application Control' in str(exc):
                line(WARN, f'  {label} was blocked by a Windows policy (Defender Application Control /')
                line(WARN, '  Device Guard) or a missing Visual C++ runtime. Ask your administrator for a')
                line(WARN, '  policy exclusion, or use a Python build whose DLLs are allowed. Do not')
                line(WARN, '  disable the policy.')
            continue
        line(CHECK, f'{label} {version(module_name)} imported')

    print('-' * 66)
    try:
        import database
        db_path = Path(database.PATH)
        line(CHECK, f'Database path: {db_path}')
        db_path.parent.mkdir(parents=True, exist_ok=True)
        database.init()
        with database.db() as conn:
            tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            endpoints = conn.execute('SELECT COUNT(*) FROM endpoints').fetchone()[0]
            checks = conn.execute('SELECT COUNT(*) FROM checks').fetchone()[0]
        missing = {'endpoints', 'checks'} - tables
        if missing:
            failures += 1
            line(CROSS, f'Missing tables: {", ".join(sorted(missing))}')
        else:
            line(CHECK, f'SQLite {sqlite3.sqlite_version} ready: {endpoints} monitors, {checks} recorded checks')
    except Exception as exc:
        failures += 1
        line(CROSS, f'Database check failed: {type(exc).__name__}: {exc}')

    try:
        if database.SUPABASE_URL and database.SUPABASE_KEY:
            line(CHECK, f'Supabase configured: {database.SUPABASE_URL}')
            if database.is_supabase_enabled():
                r_ep = database._http.get(f'{database.SUPABASE_URL}/rest/v1/endpoints?select=count', headers={'Prefer': 'count=exact'}, timeout=5)
                ep_c = r_ep.headers.get('content-range', '0/0').split('/')[-1] if r_ep.status_code in (200, 206) else '0'
                r_ch = database._http.get(f'{database.SUPABASE_URL}/rest/v1/checks?select=count', headers={'Prefer': 'count=exact'}, timeout=5)
                ch_c = r_ch.headers.get('content-range', '0/0').split('/')[-1] if r_ch.status_code in (200, 206) else '0'
                line(CHECK, f'Supabase PostgreSQL ready: {ep_c} monitors, {ch_c} recorded checks')
            else:
                line(WARN, 'Supabase tables not initialized in cloud database yet.')
                line(WARN, '  Run the SQL from supabase_schema.sql in your Supabase SQL Editor.')
                line(WARN, '  Then run: python migrate_to_supabase.py to upload existing data.')
    except Exception as exc:
        line(WARN, f'Supabase check: {exc}')

    print('-' * 66)
    try:
        import locks
        line(CHECK, f'Scheduler lock backend: {locks.BACKEND} (single scheduler per database)')
    except Exception as exc:
        failures += 1
        line(CROSS, f'Scheduler lock unavailable: {type(exc).__name__}: {exc}')

    token = os.getenv('MONITOR_TOKEN')
    if token:
        line(CHECK, f'MONITOR_TOKEN is set ({len(token)} characters, value not shown)')
    else:
        line(WARN, 'MONITOR_TOKEN is not set: the dashboard works, but add/edit/test/delete is disabled')

    for name in ('MONITOR_SECRET', 'MONITOR_TIMEOUT', 'MONITOR_DB', 'MONITOR_SECURE_COOKIE'):
        if os.getenv(name):
            line(CHECK, f'{name} is set')
    if os.getenv('MONITOR_SECURE_COOKIE') == '1':
        line(WARN, 'MONITOR_SECURE_COOKIE=1 marks session cookies Secure: only use it behind HTTPS, or login will fail')

    print('-' * 66)
    if failures:
        print(f' {failures} problem(s) found. Fix them, then run: python diagnose.py')
    else:
        print(' Everything needed for API Sentinel is available.')
        print(' Start the app with: python app.py')
    print('=' * 66)
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
