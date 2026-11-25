#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
mkdir -p backups
file="backups/production-$(date +%Y%m%d-%H%M%S).sql"
docker compose exec -T db pg_dump -U production -d production > "$file"
test -s "$file"
echo "Резервная копия: $file"
