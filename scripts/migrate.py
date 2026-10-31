#!/usr/bin/env python3
"""Apply ordered SQL migrations once and record SHA-256 checksums."""
from __future__ import annotations

import hashlib
from pathlib import Path

import psycopg

from knowhow.settings import settings

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS = ROOT / "db" / "migrations"


def apply_migrations() -> list[str]:
    applied: list[str] = []
    with psycopg.connect(settings.database_url) as conn, conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS schema_migration (
              name       TEXT PRIMARY KEY,
              checksum   TEXT NOT NULL,
              applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        for path in sorted(MIGRATIONS.glob("*.sql")):
            sql = path.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            cur.execute(
                "SELECT checksum FROM schema_migration WHERE name=%s",
                (path.name,),
            )
            record = cur.fetchone()
            if record:
                if record[0] != checksum:
                    raise RuntimeError(
                        f"Migration {path.name} changed after being applied. "
                        "Add a new migration instead."
                    )
                continue
            cur.execute(sql)
            cur.execute(
                "INSERT INTO schema_migration(name, checksum) VALUES (%s,%s)",
                (path.name, checksum),
            )
            applied.append(path.name)
        conn.commit()
    return applied


def main() -> None:
    applied = apply_migrations()
    print("migrations applied: " + (", ".join(applied) if applied else "none pending"))


if __name__ == "__main__":
    main()

