"""Tests for the cross-platform scheduler lock (Windows msvcrt / POSIX fcntl)."""
import importlib
import os
import sys
import types

import pytest

import locks


def test_lock_is_exclusive_and_reusable(tmp_path):
    path = tmp_path / 'one.lock'
    first = locks.FileLock(path)
    assert first.acquire()
    assert first.held
    assert path.read_text().strip() == str(os.getpid())

    second = locks.FileLock(path)
    assert not second.acquire()
    assert not second.held

    first.release()
    assert not first.held
    assert second.acquire()
    second.release()
    first.release()          # releasing twice must not raise


def test_context_manager_raises_when_taken(tmp_path):
    path = tmp_path / 'ctx.lock'
    with locks.FileLock(path) as held:
        assert held.held
        with pytest.raises(RuntimeError):
            with locks.FileLock(path):
                pass
    with locks.FileLock(path):
        pass


def test_separate_paths_do_not_block(tmp_path):
    first, second = locks.FileLock(tmp_path / 'a.lock'), locks.FileLock(tmp_path / 'b.lock')
    assert first.acquire() and second.acquire()
    first.release()
    second.release()


def test_windows_backend_uses_one_byte_msvcrt_lock(monkeypatch, tmp_path):
    """Simulate Windows: msvcrt byte-range locking, no fcntl module at all."""
    calls = []
    fake = types.ModuleType('msvcrt')
    fake.LK_NBLCK, fake.LK_UNLCK = 2, 0
    locked = set()

    def locking(fd, mode, nbytes):
        key = os.fstat(fd).st_ino
        if mode == fake.LK_NBLCK:
            if key in locked:
                raise OSError(13, 'Permission denied')   # the region is already locked
            locked.add(key)
        else:
            locked.discard(key)
        calls.append((mode, nbytes))

    fake.locking = locking
    monkeypatch.setitem(sys.modules, 'msvcrt', fake)
    monkeypatch.delitem(sys.modules, 'fcntl', raising=False)
    monkeypatch.setattr(os, 'name', 'nt')
    try:
        module = importlib.reload(locks)
        assert module.BACKEND == 'msvcrt'

        owner = module.FileLock(tmp_path / 'win.lock')
        assert owner.acquire()
        assert owner.acquire()                       # already held by this object
        assert (tmp_path / 'win.lock').stat().st_size >= 1
        assert not module.FileLock(tmp_path / 'win.lock').acquire()
        owner.release()
        assert not owner.held
        assert calls[0] == (fake.LK_NBLCK, 1)
        assert calls[-1] == (fake.LK_UNLCK, 1)
    finally:
        monkeypatch.undo()
        importlib.reload(locks)
    assert locks.BACKEND in {'msvcrt', 'fcntl'}
