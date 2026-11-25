"""Подставляет только известные параметры, сохраняя относительный путь конфигурации."""
import os
import sys
from pathlib import Path

root=Path(__file__).resolve().parents[1]
source=(root/'swarm/stack.yml').read_text()
for token,name,default in [('${IMAGE_API:-production-api:local}','IMAGE_API','production-api:local'),
                           ('${IMAGE_FRONTEND:-production-frontend:local}','IMAGE_FRONTEND','production-frontend:local'),
                           ('${COOKIE_SECURE:-0}','COOKIE_SECURE','0')]:
    value=os.getenv(name,default)
    if not value or any(char.isspace() for char in value):raise ValueError('Некорректное значение '+name)
    source=source.replace(token,value)
sys.stdout.write(source)
