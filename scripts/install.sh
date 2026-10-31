#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"

command -v docker >/dev/null || { echo "缺少 Docker"; exit 1; }
command -v uv >/dev/null || { echo "缺少 uv"; exit 1; }

if [ ! -f .env ]; then
  cp .env.example .env
  secret=$(openssl rand -base64 32)
  sed -i.bak "s/^KNOWHOW_SECRET=.*/KNOWHOW_SECRET=$secret/" .env
  rm -f .env.bak
fi

docker compose up -d db
for _ in $(seq 1 30); do
  docker compose exec -T db pg_isready -U knowhow -d knowhow >/dev/null 2>&1 && break
  sleep 2
done
uv sync
uv run python scripts/migrate.py
docker compose up -d --build app

echo "Knowhow 已启动：http://localhost:8000"
echo "局域网成员请从管理员“团队 → 邀请成员”获取链接。"

