#!/usr/bin/env python3
"""Apply database migrations once and record their checksums."""
from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg

from src.config import RAG_DSN

ROOT = Path(__file__).resolve().parent.parent


def _apply(cur, name: str, sql_path: Path) -> bool:
    sql = sql_path.read_text(encoding="utf-8")
    checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    cur.execute(
        "SELECT checksum FROM schema_migration WHERE name=%s",
        (name,),
    )
    applied = cur.fetchone()
    if applied:
        if applied[0] != checksum:
            raise RuntimeError(
                f"迁移 {name} 已执行但文件内容发生变化；"
                "请新增迁移文件，不要修改历史迁移"
            )
        return False
    cur.execute(sql)
    cur.execute(
        "INSERT INTO schema_migration(name, checksum) VALUES (%s,%s)",
        (name, checksum),
    )
    return True


def main() -> None:
    applied = []
    with psycopg.connect(RAG_DSN) as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_migration (
              name       TEXT PRIMARY KEY,
              checksum   TEXT NOT NULL,
              applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cur.execute("SELECT to_regclass('public.faq')")
        if cur.fetchone()[0] is None:
            if _apply(cur, "schema.sql", ROOT / "db" / "schema.sql"):
                applied.append("schema.sql")
        for name in (
            "migration_v2.sql", "migration_v3.sql", "migration_v4.sql",
            "migration_v5.sql",
        ):
            path = ROOT / "db" / name
            if path.exists() and _apply(cur, name, path):
                applied.append(name)
        conn.commit()
    print("migrations applied: " + (", ".join(applied) if applied else "none pending"))


if __name__ == "__main__":
    main()
