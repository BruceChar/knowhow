"""PostgreSQL connection helpers."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg.rows import dict_row

from knowhow.settings import settings


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        yield conn


def fetch_one(query: str, params: tuple | dict = ()) -> dict | None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()


def fetch_all(query: str, params: tuple | dict = ()) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(query, params)
        return cur.fetchall()

