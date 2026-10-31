#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

backup=$(scripts/backup.sh)
echo "备份完成：$backup"
git pull --ff-only
uv sync
uv run python scripts/migrate.py
docker compose up -d --build app

for _ in $(seq 1 30); do
  if curl -fsS http://localhost:8000/health >/dev/null; then
    echo "升级成功"
    exit 0
  fi
  sleep 2
done

echo "升级后健康检查失败。数据库备份：$backup"
exit 1

