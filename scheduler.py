"""Exactly one scheduler per database, on Windows, Linux and macOS."""
import atexit
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor

from apscheduler.schedulers.background import BackgroundScheduler

from database import PATH, db, get_due_endpoints_supabase, is_supabase_enabled
from locks import BACKEND as LOCK_BACKEND, FileLock
from monitor import record

LOG = logging.getLogger(__name__)


def start():
    """Start the dispatch loop, or return None if another process owns it.

    The database lock (see locks.py) keeps the probe loop single-instance even
    when several web workers or a separate ``--scheduler-only`` process run.
    """
    lock = FileLock(str(PATH.resolve()) + '.scheduler.lock')
    if not lock.acquire():
        LOG.warning('Another scheduler already owns %s; this process will not run probes.', lock.path)
        return None

    pool = ThreadPoolExecutor(max_workers=8)
    active = {}

    def dispatch():
        for key, future in list(active.items()):
            if future.done():
                try:
                    future.result()
                except Exception:
                    LOG.exception('Scheduled probe failed to record')
                del active[key]
        now = time.time()
        if is_supabase_enabled():
            queued = get_due_endpoints_supabase(now, active, max_pool=8)
        else:
            with db() as conn:
                rows = conn.execute('SELECT * FROM endpoints WHERE next_due<=? ORDER BY next_due', (now,)).fetchall()
                queued = []
                for row in rows:
                    if len(active) + len(queued) >= 8:
                        break
                    if row['id'] not in active:
                        changed = conn.execute('UPDATE endpoints SET next_due=? WHERE id=? AND revision=? AND next_due<=?', (now + row['interval'], row['id'], row['revision'], now)).rowcount
                        if changed:
                            queued.append(row)
        for row in queued:
            active[row['id']] = pool.submit(record, row, 'scheduled')

    scheduler = BackgroundScheduler(timezone='UTC')
    scheduler.add_job(dispatch, 'interval', seconds=1, id='dispatch', max_instances=1, coalesce=True, replace_existing=True)
    scheduler.start()
    stopped = []

    def stop():
        if stopped:
            return
        stopped.append(True)
        try:
            scheduler.shutdown(wait=True)
        except Exception:               # already stopped, or never started
            LOG.debug('Scheduler shutdown skipped', exc_info=True)
        pool.shutdown(wait=True)
        lock.release()

    atexit.register(stop)
    LOG.info('Scheduler started in pid %s (lock backend: %s).', os.getpid(), LOCK_BACKEND)
    return scheduler
