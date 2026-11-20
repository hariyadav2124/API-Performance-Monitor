"""Standalone loopback-only fixture. Never add this URL to the dashboard."""
import time
from flask import Flask, jsonify, redirect

fixture = Flask(__name__)

@fixture.get('/health')
def health():
    return jsonify(status='ok')

@fixture.get('/slow')
def slow():
    time.sleep(1)
    return jsonify(status='ok')

@fixture.get('/failure')
def failure():
    return jsonify(status='unavailable'), 503

@fixture.get('/redirect')
def redirect_to_health():
    return redirect('/health')

@fixture.get('/degraded')
def degraded():
    return jsonify(status='degraded')

if __name__ == '__main__':
    fixture.run(host='127.0.0.1', port=5101, use_reloader=False)
