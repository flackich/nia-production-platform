"""Генерирует локальный .env. Существующий файл не перезаписывается."""
import secrets
from pathlib import Path
root = Path(__file__).resolve().parents[1]
target = root / '.env'
if target.exists():
    print('.env уже существует; сохранён без изменений')
else:
    target.write_text('DB_PASSWORD=' + secrets.token_urlsafe(24) + '\nSECRET_KEY=' + secrets.token_urlsafe(48) + '\n'
                      'ADMIN_PASSWORD=DemoAdmin-2026!\nMASTER_PASSWORD=DemoMaster-2026!\nVIEWER_PASSWORD=DemoView-2026!\n')
    target.chmod(0o600)
    print('.env создан. Демонстрационный вход: admin / DemoAdmin-2026!')
