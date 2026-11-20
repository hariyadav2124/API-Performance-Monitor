"""Flask routes for the dashboard and authenticated monitor management."""
import csv
import hmac
import logging
import os
import secrets
import sqlite3
import sys
import time
from pathlib import Path

import click
import plotly
from flask import Flask, Response, abort, jsonify, render_template, request, send_from_directory, session, stream_with_context
from analyzer import iso, report
from database import (
    SUPABASE_URL,
    _http,
    add_endpoint_supabase,
    db,
    delete_endpoint_supabase,
    get_endpoint_supabase,
    init,
    is_supabase_enabled,
    seed_public_supabase,
    update_endpoint_supabase,
)
from monitor import TargetError, record, resolve

DEMO = [
    ('GitHub API', 'https://api.github.com/', 'GitHub REST API service root'),
    ('JSONPlaceholder', 'https://jsonplaceholder.typicode.com/posts/1', 'Public sample REST API'),
    ('HTTPBin', 'https://httpbin.org/get', 'Public HTTP diagnostics endpoint'),
    ('Open-Meteo', 'https://api.open-meteo.com/v1/forecast?latitude=30.9&longitude=75.85&current=temperature_2m', 'Public weather forecast API'),
]


def create_app():
    app = Flask(__name__)
    app.config.update(SECRET_KEY=os.getenv('MONITOR_SECRET') or secrets.token_hex(32), MAX_CONTENT_LENGTH=8192,
                      SESSION_COOKIE_SAMESITE='Strict', SESSION_COOKIE_SECURE=os.getenv('MONITOR_SECURE_COOKIE') == '1')
    token = os.getenv('MONITOR_TOKEN')
    try:
        init()
    except (sqlite3.Error, OSError) as exc:
        raise SystemExit(
            f'Cannot create or open the SQLite database ({exc}).\n'
            'Check that the instance folder is writable, or point MONITOR_DB at a local path, for example:\n'
            '    set MONITOR_DB=C:\\Users\\98har\\Desktop\\API_Performance_Monitor\\instance\\monitor.sqlite3'
        ) from exc

    @app.errorhandler(400)
    @app.errorhandler(401)
    @app.errorhandler(404)
    @app.errorhandler(409)
    @app.errorhandler(413)
    @app.errorhandler(415)
    @app.errorhandler(429)
    @app.errorhandler(500)
    def error(exc):
        return jsonify(error=exc.description), exc.code

    @app.before_request
    def guard():
        if request.method in {'POST', 'PATCH', 'DELETE'} and request.path.startswith('/api/'):
            if not request.is_json:
                abort(415, 'A JSON request is required.')
            if request.endpoint != 'login' and (not session.get('authorized') or not hmac.compare_digest(request.headers.get('X-CSRF-Token',''), session.get('csrf','!'))):
                abort(401, 'Unlock editing first.')

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; font-src 'self'; connect-src 'self'; worker-src blob:; object-src 'none'; base-uri 'none'"
        if request.path.startswith('/api/') or request.path == '/' or request.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-store'
            response.headers['Pragma'] = 'no-cache'
        return response

    @app.get('/')
    def index():
        return render_template('index.html')

    @app.get('/assets/plotly.min.js')
    def plotly_js():
        return send_from_directory(Path(plotly.__file__).parent / 'package_data', 'plotly.min.js')

    @app.get('/api/session')
    def auth():
        return jsonify(csrf=session.get('csrf'), enabled=bool(token))

    @app.post('/api/login')
    def login():
        value = payload().get('token')
        if not token or not isinstance(value,str) or not hmac.compare_digest(value.encode(),token.encode()):
            abort(401, 'Incorrect operator token or editing disabled.')
        session['authorized'] = True
        session['csrf'] = secrets.token_urlsafe(32)
        return auth()

    def hours():
        h = request.args.get('hours', 24, type=int)
        if h not in (1, 24, 168):
            abort(400, 'Choose 1, 24 or 168 hours.')
        return h

    def payload():
        data = request.get_json()
        if not isinstance(data, dict):
            abort(400, 'Expected a JSON object.')
        return data

    def fields():
        data = payload()
        name, url, desc = (data.get(k, '') for k in ('name','url','description'))
        interval, threshold, expected = (data.get(k,d) for k,d in [('interval',60),('threshold',500),('expected_status',None)])
        if not all(isinstance(v,str) for v in (name,url,desc)) or not 1 <= len(name.strip()) <= 80 or len(desc) > 500:
            abort(400, 'Enter a name (1–80 characters) and a description of at most 500 characters.')
        if type(interval) is not int or not 10 <= interval <= 86400 or type(threshold) is not int or not 1 <= threshold <= 120000:
            abort(400, 'Interval must be 10–86400 seconds; slow threshold must be 1–120000 ms.')
        if expected is not None and (type(expected) is not int or not 200 <= expected <= 299):
            abort(400, 'Expected status must be a 2xx code.')
        try:
            resolve(url.strip())
        except TargetError as exc:
            abort(400, str(exc))
        return name.strip(),url.strip(),desc.strip(),interval,threshold,expected

    @app.get('/api/dashboard')
    def dashboard():
        return jsonify(report(hours()))

    @app.post('/api/endpoints')
    def add():
        values = fields()
        if is_supabase_enabled():
            return jsonify(id=add_endpoint_supabase(values)), 201
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            if conn.execute('SELECT COUNT(*) FROM endpoints').fetchone()[0] >= 50:
                abort(409, 'The 50-monitor limit has been reached.')
            cursor = conn.execute('INSERT INTO endpoints(name,url,description,interval,threshold,expected_status) VALUES(?,?,?,?,?,?)',values)
            return jsonify(id=cursor.lastrowid),201

    @app.patch('/api/endpoints/<int:key>')
    def edit(key):
        values = fields()
        if is_supabase_enabled():
            if not update_endpoint_supabase(key, values):
                abort(404)
            return jsonify(ok=True)
        with db() as conn:
            conn.execute('BEGIN IMMEDIATE')
            old = conn.execute('SELECT * FROM endpoints WHERE id=?',(key,)).fetchone()
            if not old:
                abort(404)
            if old['url'] != values[1] or old['expected_status'] != values[5]:
                conn.execute('DELETE FROM checks WHERE endpoint_id=?',(key,))
            conn.execute('UPDATE endpoints SET name=?,url=?,description=?,interval=?,threshold=?,expected_status=?,revision=revision+1,next_due=0 WHERE id=?',(*values,key))
        return jsonify(ok=True)

    @app.delete('/api/endpoints/<int:key>')
    def remove(key):
        if is_supabase_enabled():
            if not delete_endpoint_supabase(key):
                abort(404)
            return jsonify(ok=True)
        with db() as conn:
            if not conn.execute('DELETE FROM endpoints WHERE id=?',(key,)).rowcount:
                abort(404)
        return jsonify(ok=True)

    @app.post('/api/endpoints/<int:key>/check')
    def check(key):
        if is_supabase_enabled():
            row = get_endpoint_supabase(key)
        else:
            with db() as conn:
                row = conn.execute('SELECT * FROM endpoints WHERE id=?',(key,)).fetchone()
        if not row:
            abort(404)
        result = record(row,'manual')
        if result is None:
            abort(409, 'Monitor changed while the check was running.')
        return jsonify(result)

    @app.get('/api/history.csv')
    def export():
        end = time.time()
        start = end-hours()*3600
        key = request.args.get('endpoint',type=int)
        def rows():
            def safe(value):
                return "'"+value if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@','\t','\r','\n')) else value
            class Echo:
                def write(self, value):
                    return value
            writer = csv.writer(Echo())
            yield writer.writerow(['timestamp_utc','name','url','latency_ms','http_status','passed_policy','error','source'])
            if is_supabase_enabled():
                url = f'{SUPABASE_URL}/rest/v1/checks?select=id,ts,latency,status,ok,error,source,endpoint_id,endpoints(name,url)&ts=gte.{start}&ts=lte.{end}&order=ts.desc,id.desc'
                if key:
                    url += f'&endpoint_id=eq.{key}'
                r = _http.get(url, timeout=20)
                items = r.json() if r.status_code == 200 else []
                for item in items:
                    ep_info = item.get('endpoints') or {}
                    yield writer.writerow([
                        iso(item['ts']),
                        safe(ep_info.get('name', '')),
                        safe(ep_info.get('url', '')),
                        item['latency'],
                        item['status'],
                        item['ok'],
                        safe(item.get('error')),
                        item['source']
                    ])
            else:
                with db() as conn:
                    for r in conn.execute('''SELECT c.*,e.name,e.url FROM checks c JOIN endpoints e ON e.id=c.endpoint_id
                        WHERE c.ts BETWEEN ? AND ? AND (? IS NULL OR e.id=?) ORDER BY c.ts DESC,c.id DESC''',(start,end,key,key)):
                        yield writer.writerow([iso(r['ts']),safe(r['name']),safe(r['url']),r['latency'],r['status'],r['ok'],safe(r['error']),r['source']])
        return Response(stream_with_context(rows()),mimetype='text/csv',headers={'Content-Disposition':'attachment; filename=api-sentinel-history.csv'})

    @app.cli.command('init-db')
    def init_db():
        init()
        click.echo('Database ready.')

    @app.cli.command('seed-public')
    def seed():
        if is_supabase_enabled():
            c = seed_public_supabase(DEMO, resolve)
            click.echo(f'{c} public monitor(s) registered on Supabase; the scheduler will record real checks.')
            return
        for name,url,desc in DEMO:
            try:
                resolve(url)
            except TargetError as exc:
                click.echo(f'Skipped {name}: {exc}')
                continue
            with db() as conn:
                conn.execute('INSERT INTO endpoints(name,url,description,interval,threshold) SELECT ?,?,?,60,500 WHERE NOT EXISTS (SELECT 1 FROM endpoints WHERE url=?)',(name,url,desc,url))
        click.echo('Public monitors registered; the scheduler will record real checks.')

    return app

def say(message):
    """Print to a Windows CMD console without crashing on odd characters."""
    try:
        print(message, flush=True)
    except UnicodeEncodeError:
        encoding = sys.stdout.encoding or 'utf-8'
        print(message.encode(encoding, 'replace').decode(encoding), flush=True)




if __name__ == '__main__':
    import argparse

    from database import PATH as DB_PATH
    from scheduler import start

    parser = argparse.ArgumentParser(description='API Sentinel - API Performance Monitor')
    parser.add_argument('--host', default='127.0.0.1', help='127.0.0.1 (local) or 0.0.0.0 (all interfaces)')
    parser.add_argument('--port', type=int, default=5000)
    parser.add_argument('--scheduler-only', action='store_true', help='run probes only, no web server')
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(levelname)s %(message)s'
    )

    # Generate a fresh operator token on every startup
    token = secrets.token_urlsafe(32)
    os.environ['MONITOR_TOKEN'] = token

    say('')
    say('=' * 66)
    say(' API SENTINEL - OPERATOR ACCESS')
    say('=' * 66)
    say(f' TOKEN: {token}')
    say(' Copy this token and paste it in Operator Access.')
    say('=' * 66)
    say('')

    # Create application AFTER setting the token
    application = create_app()
    dispatcher = start()

    editing = 'enabled (MONITOR_TOKEN is set)'

    scheduler_state = (
        'running in this process'
        if dispatcher is not None
        else 'not started - another process already owns this database'
    )

    db_label = f'Supabase ({SUPABASE_URL})' if is_supabase_enabled() else f'SQLite ({DB_PATH})'
    banner = [
        '',
        '=' * 66,
        ' API Sentinel - API Performance Monitor',
        '-' * 66,
        f' Database  : {db_label}',
        f' Scheduler : {scheduler_state}',
        f' Operator  : {editing}',
    ]

    if args.scheduler_only:
        banner += [
            ' Mode      : scheduler only (no web server)',
            ' Stop      : press CTRL+C',
            '=' * 66
        ]
    else:
        shown = '127.0.0.1' if args.host in ('0.0.0.0', '::', '') else args.host

        banner += [
            f' Dashboard : http://{shown}:{args.port}',
            ' Stop      : press CTRL+C',
            '=' * 66,
            ''
        ]

    for line in banner:
        say(line)

    if args.scheduler_only:
        if dispatcher is None:
            raise SystemExit('A scheduler already owns this database; nothing to do.')

        from threading import Event

        try:
            Event().wait()
        except KeyboardInterrupt:
            say('Stopping scheduler...')

    else:
        try:
            application.run(
                host=args.host,
                port=args.port,
                use_reloader=False
            )
        except OSError as exc:
            raise SystemExit(
                f'Could not start the web server on {args.host}:{args.port} ({exc}).\n'
                f'That port is probably already in use. Try: python app.py --port 5001'
            ) from exc
