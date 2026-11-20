import pytest
from fastapi.testclient import TestClient
from src.main import app
from src.config.database import Session
from src.models.schedule import SchedOrder, SchedAssignment
from src.services import scheduling

client = TestClient(app)

def _token(email, password='Demo2026!'):
    r = client.post('/auth/login', json={'email': email, 'password': password})
    assert r.status_code == 200, r.text
    return r.json()['token']

def _auth(token):
    return {'Authorization': 'Bearer ' + token}

def test_line_shift_and_batch_crud(seeded):
    admin = _token('admin@demo.local')
    r = client.post('/scheduling/line-shifts', json={'code': '试制线D|早班', 'name': '试制线D早班', 'capacity': 800}, headers=_auth(admin))
    assert r.status_code == 200, r.text
    r = client.get('/scheduling/line-shifts', headers=_auth(admin))
    assert r.status_code == 200 and len(r.json()) == 7
    analyst = _token('analyst@demo.local')
    # viewer 只读，不能写
    viewer = _token('viewer@demo.local')
    r = client.post('/scheduling/line-shifts', json={'code': '试制线E|早班', 'name': '试制线E早班', 'capacity': 10}, headers=_auth(viewer))
    assert r.status_code == 403
    payload = {'data': {'key': 'API-1', 'name': '接口批', 'quantity': 300, 'eligible_shift_ids': [1]}}
    r = client.post('/scheduling/batches', json=payload, headers=_auth(analyst))
    assert r.status_code == 200, r.text

def test_order_lifecycle_api(seeded, batches, fake_redis):
    analyst = _token('analyst@demo.local')
    body = {'name': 'API排产单', 'idempotency_key': 'api-idem-1', 'policy_id': seeded['policy'].id, 'batches': batches}
    r = client.post('/scheduling/orders', json=body, headers=_auth(analyst))
    assert r.status_code == 200, r.text
    oid = r.json()['id']
    # 幂等重放返回同一单
    r2 = client.post('/scheduling/orders', json=body, headers=_auth(analyst))
    assert r2.json()['id'] == oid and r2.json()['dedup_hit'] is True
    scheduling.process_one(oid)
    r = client.get('/scheduling/orders', headers=_auth(analyst))
    assert r.status_code == 200
    mine = next(o for o in r.json() if o['id'] == oid)
    assert mine['status'] == 'completed' and len(mine['assignments']) == 3
    assert mine['result']['floor_good_pieces'] >= 1
    r = client.get('/scheduling/orders/%d' % oid, headers=_auth(analyst))
    assert r.status_code == 200
    events = [e['event'] for e in r.json()['events']]
    assert 'created' in events and 'commit' in events

def test_rejected_order_shows_reason(seeded, fake_redis):
    analyst = _token('analyst@demo.local')
    bad = [{'key': 'API-BAD', 'name': '超大', 'quantity': 999999, 'eligible_shift_ids': [seeded['shifts'][0].id]}]
    r = client.post('/scheduling/orders', json={'name': '退回单', 'idempotency_key': 'api-idem-bad', 'policy_id': seeded['policy'].id, 'batches': bad}, headers=_auth(analyst))
    oid = r.json()['id']
    scheduling.MAX_ATTEMPTS = 1
    try:
        scheduling.process_one(oid)
    finally:
        scheduling.MAX_ATTEMPTS = 5
    r = client.get('/scheduling/orders/%d' % oid, headers=_auth(analyst))
    data = r.json()
    assert data['status'] == 'rejected' and data['reject_reason']
    assert data['assignments'] == []

def test_history_yield_endpoint(seeded):
    token = _token('viewer@demo.local')
    r = client.get('/scheduling/history-yield', headers=_auth(token))
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 10
    # 每天覆盖 6 个线班，良率在 (0,1)
    for day, rates in data.items():
        assert len(rates) == 6
        assert all(0 < v < 1 for v in rates.values())

def test_requires_auth(seeded):
    assert client.get('/scheduling/orders').status_code == 401
