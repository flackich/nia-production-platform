"""Развёртывание изолированного двухузлового Swarm. Требуется запущенный Docker."""
import io
import os
import re
from pathlib import Path
import subprocess
import tarfile
import time

root = Path(__file__).resolve().parents[1]
docker = os.getenv('DOCKER_BIN', 'docker')
prefix = [docker, 'compose', '-f', str(root / 'swarm/lab.compose.yml')]


def run(args, **kw):
    result = subprocess.run(args, text=True, capture_output=True, cwd=root, **kw)
    if result.returncode:
        raise RuntimeError(result.stderr.strip())
    return result.stdout.strip()


def inner(node, *args, data=None):
    return run(prefix + ['exec', '-T', node, 'docker', *args], input=data)


run(prefix + ['up', '-d'])
for node in ('manager', 'worker'):
    for attempt in range(60):
        try: inner(node, 'info');break
        except RuntimeError: time.sleep(2)
    else: raise RuntimeError('Docker Engine не запустился: ' + node)
    state = inner(node, 'info', '--format', '{{.Swarm.LocalNodeState}}')
    if node == 'manager' and state == 'inactive':
        ip = run(prefix + ['exec','-T',node,'hostname','-i']).split()[0]
        inner(node, 'swarm', 'init', '--advertise-addr', ip)
    if node == 'worker' and state == 'inactive':
        token = inner('manager','swarm','join-token','-q','worker')
        inner(node,'swarm','join','--token',token,'manager:2377')
manager = inner('manager','info','--format','{{.Swarm.NodeID}}')
inner('manager','node','update','--label-add','production_data=true',manager)
images = ['production-api:local','production-frontend:local','postgres:17.6-alpine','redis:7.4.6-alpine','prom/prometheus:v3.7.3']
print('Передача образов на два узла', flush=True)
for node in ('manager','worker'):
    producer = subprocess.Popen([docker,'save',*images], stdout=subprocess.PIPE, cwd=root)
    consumer = subprocess.run(prefix+['exec','-T',node,'docker','load'], stdin=producer.stdout,
                              capture_output=True, cwd=root)
    producer.stdout.close()
    producer_code = producer.wait()
    if consumer.returncode or producer_code:
        raise RuntimeError(consumer.stderr.decode())
    print(node + ': образы загружены', flush=True)
env = dict(line.split('=',1) for line in (root/'.env').read_text().splitlines() if '=' in line)
for name,key in [('db_password','DB_PASSWORD'),('session_key','SECRET_KEY'),('admin_password','ADMIN_PASSWORD'),('master_password','MASTER_PASSWORD'),('viewer_password','VIEWER_PASSWORD')]:
    if name not in inner('manager','secret','ls','--format','{{.Name}}').splitlines():
        inner('manager','secret','create',name,'-',data=env[key])
# Конфигурация и файл Prometheus передаются внутрь manager без подстановки секретов.
run(prefix+['exec','-T','manager','mkdir','-p','/lab/swarm','/lab/monitoring'])
for local,remote in [(root/'swarm/stack.yml','/lab/swarm/stack.yml'),(root/'monitoring/prometheus.yml','/lab/monitoring/prometheus.yml')]:
    run(prefix+['exec','-T','manager','sh','-c','cat > '+remote],input=local.read_text())
print(inner('manager','stack','deploy','--resolve-image','never','-c','/lab/swarm/stack.yml','production'),flush=True)
for service in ('production_frontend','production_api','production_worker'):
    inner('manager','service','update','--force','--detach',service)
for _ in range(90):
    states=inner('manager','service','ls','--format','{{.Replicas}}').splitlines()
    counts=[re.match(r'^(\d+)/(\d+)',s) for s in states]
    if len(counts)==6 and all(m and m[1]==m[2] for m in counts):break
    time.sleep(2)
else:
    print(inner('manager','stack','ps','production','--no-trunc'))
    raise RuntimeError('Кластер не достиг ожидаемого состояния')
print(inner('manager','node','ls'),flush=True)
print(inner('manager','service','ls'),flush=True)
print('Открыть http://localhost:18080',flush=True)
