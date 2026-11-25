import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'app'))
from server import create_app


@pytest.fixture()
def client(tmp_path):
    app = create_app({'TESTING': True, 'DATABASE_URL': 'sqlite:///' + str(tmp_path / 'test.db')})
    app.extensions['cache'].connection_pool.connection_kwargs.update(socket_connect_timeout=.01, socket_timeout=.01)
    return app.test_client()


def login(client, role='admin'):
    csrf = client.get('/api/session').json['csrf']
    password = {'admin': 'DemoAdmin-2026!', 'master': 'DemoMaster-2026!', 'viewer': 'DemoView-2026!'}[role]
    r = client.post('/api/login', json={'username': role, 'password': password}, headers={'X-CSRF-Token': csrf})
    assert r.status_code == 200
    return {'X-CSRF-Token': r.json['csrf']}


def payload(**changes):
    return dict(code='TEST-001', product='Испытательная деталь', quantity=20, completed_quantity=0,
                equipment_id=1, status='planned', priority='normal', due_date='2026-12-14', **changes)


def test_health(client):
    assert client.get('/health/live').json['status'] == 'ok'
    assert client.get('/health/ready').status_code == 200


def test_auth_required(client):
    assert client.get('/api/orders').status_code == 401


def test_csrf_required(client):
    assert client.post('/api/login', json={'username': 'admin'}).status_code == 403


def test_wrong_password(client):
    token = client.get('/api/session').json['csrf']
    assert client.post('/api/login', json={'username': 'admin', 'password': 'wrong'}, headers={'X-CSRF-Token': token}).status_code == 401


def test_seed_and_filters(client):
    login(client)
    assert len(client.get('/api/orders').json) == 6
    assert len(client.get('/api/equipment').json) == 4
    assert len(client.get('/api/orders?status=in_progress').json) == 2
    assert len(client.get('/api/orders?q=редуктора').json) == 1
    assert client.get('/api/orders?status=invalid').status_code == 400


def test_crud_and_audit(client):
    headers = login(client)
    r = client.post('/api/orders', json=payload(), headers=headers)
    assert r.status_code == 201
    order = r.json
    order['product'] = 'Изменённая деталь'
    r = client.put('/api/orders/' + str(order['id']), json=order, headers=headers)
    assert r.status_code == 200 and r.json['version'] == 2
    assert client.delete('/api/orders/' + str(order['id']), headers=headers, content_type='application/json').status_code == 204
    actions = [a['action'] for a in client.get('/api/audit').json]
    assert all(a in actions for a in ['order.create', 'order.update', 'order.delete'])


@pytest.mark.parametrize('change', [
    {'quantity': 0}, {'quantity': -1}, {'quantity': 1.5}, {'quantity': True},
    {'completed_quantity': 30}, {'code': ''}, {'product': ''}, {'equipment_id': 999},
    {'due_date': 'wrong'}, {'status': 'completed'}, {'priority': 'invalid'},
])
def test_invalid_order(client, change):
    headers = login(client)
    data = payload();data.update(change)
    assert client.post('/api/orders', json=data, headers=headers).status_code == 400


def test_unique_code(client):
    h = login(client)
    assert client.post('/api/orders', json=payload(), headers=h).status_code == 201
    assert client.post('/api/orders', json=payload(), headers=h).status_code == 409


def test_optimistic_lock(client):
    h = login(client)
    order = client.post('/api/orders', json=payload(), headers=h).json
    assert client.put('/api/orders/' + str(order['id']), json=order, headers=h).status_code == 200
    assert client.put('/api/orders/' + str(order['id']), json=order, headers=h).status_code == 409


def test_lifecycle(client):
    h = login(client)
    order = client.post('/api/orders', json=payload(), headers=h).json
    order['status'] = 'completed';order['completed_quantity'] = 20
    assert client.put('/api/orders/' + str(order['id']), json=order, headers=h).status_code == 400
    order['status'] = 'in_progress';order['completed_quantity'] = 5
    order = client.put('/api/orders/' + str(order['id']), json=order, headers=h).json
    assert order['status'] == 'in_progress'
    order['status'] = 'completed'
    assert client.put('/api/orders/' + str(order['id']), json=order, headers=h).status_code == 400
    order['completed_quantity'] = 20
    r = client.put('/api/orders/' + str(order['id']), json=order, headers=h)
    assert r.status_code == 200
    assert client.put('/api/orders/' + str(order['id']), json=r.json, headers=h).status_code == 400


@pytest.mark.parametrize('role,create,remove,audit,system', [('viewer',403,403,403,403), ('master',201,403,200,403), ('admin',201,204,200,200)])
def test_roles(client, role, create, remove, audit, system):
    h = login(client, role)
    assert client.post('/api/orders', json=payload(), headers=h).status_code == create
    assert client.delete('/api/orders/2', headers=h).status_code == remove
    assert client.get('/api/audit').status_code == audit
    assert client.get('/api/system').status_code == system


def test_machine_maintenance(client):
    h = login(client)
    order = client.post('/api/orders', json=payload(), headers=h).json
    assert client.put('/api/equipment/1', json={'status':'maintenance'}, headers=h).status_code == 200
    order['status'] = 'in_progress'
    assert client.put('/api/orders/' + str(order['id']), json=order, headers=h).status_code == 400


def test_cannot_delete_active(client):
    h = login(client)
    assert client.delete('/api/orders/1', headers=h).status_code == 409
    assert client.delete('/api/orders/999', headers=h).status_code == 404


def test_summary_when_redis_unavailable(client):
    login(client)
    s = client.get('/api/summary').json
    assert s['total'] == 6 and s['produced_units'] == 315 and s['planned_units'] == 700


def test_csv_and_formula_injection(client):
    h = login(client)
    data = payload();data['product'] = '=1+1'
    assert client.post('/api/orders', json=data, headers=h).status_code == 201
    r = client.get('/api/export')
    assert r.status_code == 200 and "'=1+1" in r.data.decode()


def test_metrics(client):
    r = client.get('/metrics')
    assert r.status_code == 200 and b'production_orders_current 6' in r.data


def test_logout(client):
    h = login(client)
    assert client.post('/api/logout', headers=h).status_code == 200
    assert client.get('/api/orders').status_code == 401
