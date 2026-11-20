"""The environment checker must run cleanly against a fresh database."""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_diagnose_reports_a_healthy_environment(tmp_path):
    env = {**os.environ, 'MONITOR_DB': str(tmp_path / 'diagnose.sqlite3'), 'MONITOR_TOKEN': 'test-token'}
    result = subprocess.run([sys.executable, 'diagnose.py'], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'Everything needed for API Sentinel is available.' in result.stdout
    assert 'test-token' not in result.stdout          # the token is never printed
    assert (tmp_path / 'diagnose.sqlite3').exists()   # init-db ran


def test_diagnose_flags_a_missing_token(tmp_path):
    env = {**os.environ, 'MONITOR_DB': str(tmp_path / 'diagnose2.sqlite3')}
    env.pop('MONITOR_TOKEN', None)
    result = subprocess.run([sys.executable, 'diagnose.py'], cwd=ROOT, env=env, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0
    assert 'MONITOR_TOKEN is not set' in result.stdout
