import time
import pytest
import app as web
import database
import monitor
import scheduler

@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / 'test.sqlite'
    monkeypatch.setattr(database,'PATH',path)
    monkeypatch.setattr(scheduler,'PATH',path)
    monkeypatch.setenv('MONITOR_TOKEN','test-token')
    monkeypatch.setattr(web,'resolve',lambda url: None if url.startswith('https://example.com/') else monitor.resolve(url))
    return web.create_app().test_client()


def login(client):
    token = client.post('/api/login',json={'token':'test-token'}).json['csrf']
    return {'X-CSRF-Token':token}


def test_crud_and_csv(client, monkeypatch):
    payload = {'name':'=Test','url':'https://example.com/test','description':'Example monitor','interval':60}
    assert client.post('/api/endpoints',json=payload).status_code==401
    assert client.post('/api/login',json={'token':'wrong'}).status_code==401
    headers=login(client)
    assert client.post('/api/endpoints',json=payload).status_code==401
    assert client.post('/api/endpoints',json={**payload,'url':'http://localhost'},headers=headers).status_code==400
    created=client.post('/api/endpoints',json=payload,headers=headers)
    assert created.status_code==201
    key=created.json['id']
    assert client.get('/api/dashboard').json['uptime'] is None
    # Deterministic route/analytics contract; probe transport has its own integration tests.
    monkeypatch.setattr(monitor,'probe',lambda *_:dict(status=200,latency=31,ok=True,error=None))
    assert client.post(f'/api/endpoints/{key}/check',json={},headers=headers).json['ok']
    monkeypatch.setattr(monitor,'probe',lambda *_:dict(status=503,latency=83,ok=False,error='HTTP 503'))
    assert not client.post(f'/api/endpoints/{key}/check',json={},headers=headers).json['ok']
    d=client.get('/api/dashboard?hours=1').json
    assert d['uptime']==50 and d['average']==31 and d['bad']==1 and not d['endpoints'][0]['latest_ok']
    assert d['traces'][0]['y']==[31,None] and d['errors'][0]['status']==503
    assert "'=Test" in client.get('/api/history.csv?hours=1').text
    assert client.patch(f'/api/endpoints/{key}',json={**payload,'interval':15},headers=headers).status_code==200
    assert client.get('/api/dashboard').json['endpoints'][0]['interval']==15
    assert client.patch(f'/api/endpoints/{key}',json={**payload,'expected_status':204},headers=headers).status_code==200
    assert client.get('/api/dashboard').json['checks']==0
    assert client.delete(f'/api/endpoints/{key}',json={},headers=headers).status_code==200
    assert client.get('/api/dashboard').json['endpoints']==[]


def test_scheduler_singleton_and_records(client, monkeypatch):
    headers=login(client)
    client.post('/api/endpoints',json={'name':'Schedule','url':'https://example.com/schedule','interval':60},headers=headers)
    monkeypatch.setattr(monitor,'probe',lambda *_:dict(status=200,latency=19,ok=True,error=None))
    import atexit
    cleanups=[]
    monkeypatch.setattr(atexit,'register',lambda callback:cleanups.append(callback))
    assert scheduler.start() is not None
    try:
        assert scheduler.start() is None
        deadline=time.monotonic()+4
        while time.monotonic()<deadline:
            dashboard=client.get('/api/dashboard').json
            if dashboard['checks']:
                break
            time.sleep(.1)
        assert dashboard['checks']==1
        with database.db() as db:
            assert db.execute('SELECT source FROM checks').fetchone()['source']=='scheduled'
            assert db.execute('SELECT next_due FROM endpoints').fetchone()['next_due']>time.time()+50
    finally:
        for cleanup in cleanups:
            cleanup()


def test_window_boundaries_and_downtime(client, monkeypatch):
    from types import SimpleNamespace
    import analyzer
    end = 1800000000
    monkeypatch.setattr(analyzer, 'time', SimpleNamespace(time=lambda: end))
    with database.db() as conn:
        key = conn.execute("INSERT INTO endpoints(name,url,description,interval,threshold) VALUES('Test','https://example.com','',60,20)").lastrowid
        rows = [(key,end-4000,12,503,0,'HTTP 503','scheduled'),
                (key,end-3500,21,200,1,None,'scheduled'),
                (key,end-20,14,503,0,'HTTP 503','manual')]
        conn.executemany('INSERT INTO checks(endpoint_id,ts,latency,status,ok,error,source) VALUES(?,?,?,?,?,?,?)', rows)
    data = client.get('/api/dashboard?hours=1').json
    ep = data['endpoints'][0]
    assert ep['downtime']==120  # 100 s carried in, 20 s continuing at window end
    assert data['checks']==2 and ep['uptime']==50 and ep['slow']
