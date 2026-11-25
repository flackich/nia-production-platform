.PHONY: setup up down test smoke logs backup
setup:
	python3 scripts/setup.py
up:
	docker compose up -d --build --wait
down:
	docker compose down
test:
	python3 -m pytest -q tests
smoke:
	python3 scripts/smoke.py
logs:
	docker compose logs --tail=50
backup:
	sh scripts/backup.sh
