"""Password hashing and database-backed sessions."""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import psycopg
from psycopg.rows import dict_row

from knowhow.settings import settings

ITERATIONS = 260_000


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, ITERATIONS,
    )
    return f"pbkdf2${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations, salt_hex, digest_hex = encoded.split("$")
        if scheme != "pbkdf2":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"),
            bytes.fromhex(salt_hex), int(iterations),
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (AttributeError, TypeError, ValueError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(account_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=settings.session_hours)
    with psycopg.connect(settings.database_url) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO session(token_hash,account_id,expires_at) VALUES (%s,%s,%s)",
            (token_hash(token), account_id, expires),
        )
        conn.commit()
    return token


def session_account(token: str) -> dict | None:
    if not token:
        return None
    query = """
        SELECT a.id, a.organization_id, a.username, a.display_name, a.role,
               a.must_change_password,
               o.name AS organization_name, o.default_language
        FROM session s
        JOIN account a ON a.id=s.account_id
        JOIN organization o ON o.id=a.organization_id
        WHERE s.token_hash=%s AND s.expires_at>now() AND a.enabled
    """
    with psycopg.connect(settings.database_url, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(query, (token_hash(token),))
            account = cur.fetchone()
            if account:
                cur.execute(
                    "UPDATE session SET last_seen_at=now() WHERE token_hash=%s",
                    (token_hash(token),),
                )
                conn.commit()
    return account


def revoke_session(token: str) -> None:
    with psycopg.connect(settings.database_url) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM session WHERE token_hash=%s", (token_hash(token),))
        conn.commit()

