#!/usr/bin/env python3
"""Preflight diagnostics for a Knowhow installation."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import psycopg

from knowhow.settings import settings

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    failures = 0
    print(f"Python: {sys.version.split()[0]}")
    print(f"Project: {ROOT}")
    print(f"Database: {settings.database_url}")
    for command in ("docker", "uv"):
        ok = bool(shutil.which(command))
        print(f"{'OK' if ok else 'FAIL'} {command}")
        failures += 0 if ok else 1
    try:
        with psycopg.connect(settings.database_url, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT extversion FROM pg_extension WHERE extname='vector'")
                pgvector = cur.fetchone()
                cur.execute("SELECT count(*) FROM schema_migration")
                migrations = cur.fetchone()[0]
                cur.execute("SELECT count(*) FROM organization")
                organizations = cur.fetchone()[0]
        print(f"OK PostgreSQL; pgvector={pgvector[0] if pgvector else 'missing'}")
        print(f"OK migrations={migrations}; organizations={organizations}")
        failures += 0 if pgvector else 1
    except Exception as exc:
        print(f"FAIL database: {exc}")
        failures += 1
    try:
        import sentence_transformers  # noqa: F401

        print("OK sentence-transformers")
    except Exception as exc:
        print(f"WARN embedding runtime: {exc}")
    backup_dir = ROOT / "data" / "backups"
    print(f"{'OK' if backup_dir.exists() else 'WARN'} backup directory {backup_dir}")
    print("RESULT:", "ready" if failures == 0 else f"{failures} blocking issue(s)")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

