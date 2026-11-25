"""Интеграционная проверка работающего HTTP-стенда, без сторонних библиотек."""
import http.cookiejar
import json
import os
import sys
import time
import urllib.error
import urllib.request

base = sys.argv[1] if len(sys.argv) > 1 else 'http://127.0.0.1:8080'
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
csrf = ''
results = []


def call(path, method='GET', data=None, expected=200):
    req = urllib.request.Request(base + path, data=json.dumps(data).encode() if data is not None else None,
                                 method=method, headers={'Content-Type':'application/json','X-CSRF-Token':csrf})
    try:
        response = opener.open(req, timeout=10);code=response.status;raw=response.read()
    except urllib.error.HTTPError as error:
        code=error.code;raw=error.read()
    assert code == expected, (path, code, raw)
    results.append({'method':method, 'path':path, 'status':code})
    return json.loads(raw) if raw and not path.endswith('export') else None


call('/api/orders', expected=401)
csrf = call('/api/session')['csrf']
csrf = call('/api/login', 'POST', {'username':'admin','password':os.getenv('ADMIN_PASSWORD','DemoAdmin-2026!')})['csrf']
orders = call('/api/orders');assert len(orders) >= 6
machines=call('/api/equipment');assert len(machines) == 4
data={'code':'SMOKE-'+str(time.time_ns()),'product':'Контрольная деталь','quantity':10,'completed_quantity':0,
      'equipment_id':1,'status':'planned','priority':'normal','due_date':'2026-12-14'}
order=call('/api/orders','POST',data,201)
order['product']='Контрольная деталь (проверено)'
updated=call('/api/orders/'+str(order['id']),'PUT',order)
assert updated['version']==2
call('/api/orders/'+str(order['id']),'PUT',order,409)
call('/api/orders/'+str(order['id']),'DELETE',expected=204)
system=call('/api/system');assert system['database']=='postgresql' and system['redis'] and system['worker_alive'], system
assert call('/api/audit')
call('/api/export')
call('/api/logout','POST',{})
call('/api/orders',expected=401)
print(json.dumps({'status':'passed','checks':len(results),'database':system['database'],'redis':True,'worker':True,'results':results},ensure_ascii=False,indent=2))
