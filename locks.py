"""Cross-platform lock so only one scheduler runs per database.

The scheduler must not run twice, otherwise every endpoint would be probed two
or more times per interval. Linux/macOS use ``fcntl.flock``; Windows has no
``fcntl`` module, so it uses ``msvcrt.locking`` (the same idea, implemented with
Windows byte-range locks). Both are advisory locks owned by the operating
system: if a process crashes or is killed, the lock disappears with it, so a
stale lock can never block the next start.

Usage:
    lock = FileLock('instance/monitor.sqlite3.scheduler.lock')
    if not lock.acquire():
        ...      # another process owns it
    lock.release()
"""
import logging
import os

LOG = logging.getLogger(__name__)

# The lock covers one byte. We lock byte offset 1024 so bytes 0..63 remain
# readable by troubleshooting tools (and tests) on Windows without PermissionError.
RECORD_SIZE = 64
LOCK_OFFSET = 1024


if os.name == 'nt':                     # Windows 10 / 11
    import msvcrt

    BACKEND = 'msvcrt'

    def _acquire(handle):
        """Try to lock byte at LOCK_OFFSET without waiting. Returns True on success."""
        try:
            handle.seek(LOCK_OFFSET)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:                 # already locked by another process
            return False

    def _release(handle):
        try:
            handle.seek(LOCK_OFFSET)
            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        except OSError:                 # closing the file releases it anyway
            LOG.debug('Scheduler lock was already released')

else:                                   # Linux, macOS, BSD
    import fcntl

    BACKEND = 'fcntl'

    def _acquire(handle):
        """Try to lock the file without waiting. Returns True on success."""
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:                 # EWOULDBLOCK: lock is held elsewhere
            return False

    def _release(handle):
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            LOG.debug('Scheduler lock was already released')


class FileLock:
    """An OS-level lock held open for the lifetime of this object."""

    def __init__(self, path):
        self.path = str(path)
        self.handle = None

    @property
    def held(self):
        return self.handle is not None

    def acquire(self):
        """Return True if this process now owns the lock, False if it is taken."""
        if self.handle is not None:
            return True
        # 'r+b' keeps real file offsets; append mode would ignore seek() and
        # write past the locked byte instead of in front of it.
        flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_BINARY', 0)
        handle = os.fdopen(os.open(self.path, flags, 0o644), 'r+b')
        try:
            # msvcrt locks a byte range, and locking a byte that does not exist
            # yet is not portable, so make sure the file is at least 1 byte.
            if handle.seek(0, os.SEEK_END) == 0:
                handle.write(b'.')
                handle.flush()
            handle.seek(0)
        except OSError:                 # another process is writing it right now
            LOG.debug('Could not pre-size the scheduler lock file', exc_info=True)
        if not _acquire(handle):
            handle.close()
            return False
        self.handle = handle
        try:                            # record the owner for troubleshooting
            handle.seek(0)
            handle.write(f'{os.getpid()}'.encode().ljust(RECORD_SIZE, b' '))
            handle.flush()
        except OSError:
            LOG.debug('Could not write the owner PID to the lock file', exc_info=True)
        return True

    def release(self):
        """Release the lock. Safe to call more than once."""
        handle, self.handle = self.handle, None
        if handle is None:
            return
        _release(handle)
        handle.close()

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(f'Another process holds {self.path}')
        return self

    def __exit__(self, *exc_info):
        self.release()
