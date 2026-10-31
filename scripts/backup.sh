#!/usr/bin/env sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
cd "$ROOT"
mkdir -p data/backups
stamp=$(date +%Y%m%d_%H%M%S)
target="data/backups/knowhow_${stamp}.dump.gz"

docker compose exec -T db pg_dump -U knowhow -d knowhow --format=custom \
  | gzip > "$target"
echo "$target"

