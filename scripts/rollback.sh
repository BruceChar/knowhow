#!/usr/bin/env sh
set -eu

if [ "$#" -ne 1 ]; then
  echo "用法: scripts/rollback.sh <git-commit>"
  exit 2
fi

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
git checkout "$1"
uv sync
docker compose up -d --build app
echo "应用代码已回退到 $1。数据库请按备份恢复，不会自动降级。"

