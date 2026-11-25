"""Проверяет SQL-дамп в собственной временной базе и сравнивает число записей."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

root=Path(__file__).resolve().parents[1]
prefix=[os.getenv('DOCKER_BIN','docker'),'compose','exec','-T','db']


def run(args,data=None):
    return subprocess.run(prefix+args,input=data,check=True,capture_output=True,cwd=root).stdout


query="SELECT json_build_object('orders',(SELECT count(*) FROM orders),'equipment',(SELECT count(*) FROM equipment));"
before=json.loads(run(['psql','-U','production','-d','production','-At','-c',query]))
dump=run(['pg_dump','-U','production','-d','production'])
assert dump
name='production_verify_'+str(time.time_ns())
run(['createdb','-U','production',name])
try:
    run(['psql','-v','ON_ERROR_STOP=1','-U','production','-d',name],dump)
    after=json.loads(run(['psql','-U','production','-d',name,'-At','-c',query]))
    assert before==after,(before,after)
finally:
    run(['dropdb','-U','production',name])
print(json.dumps({'status':'passed','backup_bytes':len(dump),'sha256':hashlib.sha256(dump).hexdigest(),
                  'source_counts':before,'restored_counts':after},ensure_ascii=False,indent=2))
