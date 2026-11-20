"""Windowed metrics and Plotly-ready data, computed only from persisted probes."""
from datetime import datetime, timezone
import time
import pandas as pd
from database import SUPABASE_URL, _http, db, get_endpoints_supabase, is_supabase_enabled

COLORS = ['#67e8b1', '#a99aff', '#59bfff', '#f6bc75', '#f284ae']


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat() if ts is not None else None


def compute_metrics(endpoints, frame, prior, start, end):
    traces = []
    success = frame[frame.ok == 1] if len(frame) else pd.DataFrame(columns=['latency'])
    for ep in endpoints:
        rows = frame[frame.endpoint_id == ep['id']] if len(frame) else pd.DataFrame(columns=['ts', 'ok', 'latency'])
        good = rows[rows.ok == 1]
        state, cursor, downtime = prior.get(ep['id']), start, 0
        for t, ok in rows[['ts', 'ok']].itertuples(index=False, name=None):
            if state == 0:
                downtime += t - cursor
            state, cursor = ok, t
        if state == 0:
            downtime += end - cursor
        ep.update(
            count=len(rows),
            good=len(good),
            bad=len(rows) - len(good),
            uptime=round(len(good) * 100 / len(rows), 2) if len(rows) else None,
            average=round(float(good.latency.mean()), 1) if len(good) else None,
            downtime=round(downtime),
            latest_at=iso(ep.pop('latest_ts', None))
        )
        ep['slow'] = ep['average'] is not None and ep['average'] >= ep['threshold']
        if len(rows):
            # Bound the chart payload; full raw history is available via CSV.
            sample = rows.iloc[-1500:]
            traces.append(dict(
                id=ep['id'],
                name=ep['name'],
                x=[iso(t) for t in sample.ts],
                y=[float(lat) if ok else None for lat, ok in zip(sample.latency, sample.ok)],
                color=COLORS[(ep['id'] - 1) % len(COLORS)]
            ))
    errors = []
    if len(frame):
        bad_checks = frame[frame.ok == 0]
        errors = [
            dict(
                endpoint_id=int(r.endpoint_id),
                ts=iso(r.ts),
                status=int(r.status) if pd.notna(r.status) else None,
                error=r.error,
                source=r.source
            )
            for r in bad_checks.tail(30).iloc[::-1].itertuples()
        ]
    return dict(
        endpoints=endpoints,
        traces=traces,
        errors=errors,
        start=iso(start),
        end=iso(end),
        uptime=round(len(success) * 100 / len(frame), 2) if len(frame) else None,
        average=round(float(success.latency.mean()), 1) if len(success) else None,
        checks=len(frame),
        good=len(success),
        bad=len(frame) - len(success)
    )


def report(hours):
    end = time.time()
    start = end - hours * 3600

    if is_supabase_enabled():
        try:
            endpoints = get_endpoints_supabase()
            r = _http.get(
                f'{SUPABASE_URL}/rest/v1/checks?ts=gte.{start}&ts=lte.{end}&order=ts.asc,id.asc&limit=50000',
                timeout=15
            )
            checks = r.json() if r.status_code == 200 else []
            frame = pd.DataFrame(checks) if checks else pd.DataFrame(
                columns=['id', 'endpoint_id', 'ts', 'latency', 'status', 'ok', 'error', 'source']
            )
            if len(frame):
                frame['id'] = frame['id'].astype(int)
                frame['endpoint_id'] = frame['endpoint_id'].astype(int)
                frame['ts'] = frame['ts'].astype(float)
                frame['latency'] = frame['latency'].astype(float)
                frame['ok'] = frame['ok'].astype(int)

            prior = {}
            for ep in endpoints:
                r_p = _http.get(
                    f'{SUPABASE_URL}/rest/v1/checks?endpoint_id=eq.{ep["id"]}&ts=lt.{start}&order=ts.desc,id.desc&limit=1',
                    timeout=5
                )
                prior_rows = r_p.json() if r_p.status_code == 200 else []
                prior[ep['id']] = prior_rows[0]['ok'] if prior_rows else None

                ep_rows = frame[frame.endpoint_id == ep['id']] if len(frame) else []
                if len(ep_rows):
                    last = ep_rows.iloc[-1]
                    ep['latest_ok'] = bool(last.ok)
                    ep['latest_status'] = int(last.status) if pd.notna(last.status) else None
                    ep['latest_ts'] = float(last.ts)
                    ep['latest_error'] = last.error if pd.notna(last.error) else None
                    ep['latest_latency'] = float(last.latency)
                else:
                    r_l = _http.get(
                        f'{SUPABASE_URL}/rest/v1/checks?endpoint_id=eq.{ep["id"]}&order=ts.desc,id.desc&limit=1',
                        timeout=5
                    )
                    lat_rows = r_l.json() if r_l.status_code == 200 else []
                    if lat_rows:
                        l = lat_rows[0]
                        ep['latest_ok'] = bool(l['ok'])
                        ep['latest_status'] = l.get('status')
                        ep['latest_ts'] = l.get('ts')
                        ep['latest_error'] = l.get('error')
                        ep['latest_latency'] = l.get('latency')
                    else:
                        ep['latest_ok'] = None
                        ep['latest_status'] = None
                        ep['latest_ts'] = None
                        ep['latest_error'] = None
                        ep['latest_latency'] = None

            return compute_metrics(endpoints, frame, prior, start, end)
        except Exception:
            # Fallback to local SQLite if Supabase transiently fails
            pass

    # SQLite execution
    with db() as conn:
        conn.execute('BEGIN')
        endpoints = [
            dict(r) for r in conn.execute('''
                SELECT e.*, c.ok latest_ok, c.status latest_status, c.ts latest_ts,
                       c.error latest_error, c.latency latest_latency
                FROM endpoints e
                LEFT JOIN checks c ON c.id = (
                    SELECT id FROM checks WHERE endpoint_id = e.id ORDER BY ts DESC, id DESC LIMIT 1
                )
                ORDER BY e.id
            ''')
        ]
        frame = pd.read_sql_query(
            'SELECT * FROM checks WHERE ts>=? AND ts<=? ORDER BY ts, id',
            conn,
            params=(start, end)
        )
        prior = {
            r['id']: r['ok']
            for r in conn.execute('''
                SELECT e.id, c.ok
                FROM endpoints e
                LEFT JOIN checks c ON c.id = (
                    SELECT id FROM checks WHERE endpoint_id = e.id AND ts < ? ORDER BY ts DESC, id DESC LIMIT 1
                )
            ''', (start,))
        }

    return compute_metrics(endpoints, frame, prior, start, end)
