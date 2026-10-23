#!/usr/bin/env bash
# knowhow 数据库备份脚本
# 用法：scripts/backup.sh [保留份数]     默认保留 14 份，输出到 data/backups/
# 建议：cron/launchd 每日执行一次
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
BACKUP_DIR="${PROJECT_DIR}/data/backups"
KEEP="${1:-14}"
STAMP="$(date +%Y%m%d_%H%M%S)"

# 加载 .env 拿 RAG_DSN（无则用开发默认）
if [ -f "${PROJECT_DIR}/.env" ]; then
  set -a; . "${PROJECT_DIR}/.env"; set +a
fi
DSN="${RAG_DSN:-postgresql://rag:rag_dev_2026@localhost:5433/rag}"

mkdir -p "${BACKUP_DIR}"
echo "备份数据库 → ${BACKUP_DIR}/knowhow_${STAMP}.dump"

# 注意：容器内执行 pg_dump，避免宿主机没有客户端
docker exec rag_db pg_dump -U rag -d rag -Fc \
  | gzip > "${BACKUP_DIR}/knowhow_${STAMP}.dump.gz"

echo "清理旧备份（保留 ${KEEP} 份）..."
ls -t "${BACKUP_DIR}"/knowhow_*.dump.gz 2>/dev/null | tail -n +$((KEEP+1)) | xargs -r rm -f
echo "完成。当前备份："
ls -lh "${BACKUP_DIR}" | tail -5
