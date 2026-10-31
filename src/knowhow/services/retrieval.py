"""Small-company lexical retrieval with CJK tokenization."""
from __future__ import annotations

import re

import jieba

from knowhow.db import connect

_WORD = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]*|[\u3400-\u9fff]+")


def tokens(text: str) -> set[str]:
    result: set[str] = set()
    for part in _WORD.findall((text or "").lower()):
        if re.fullmatch(r"[\u3400-\u9fff]+", part):
            result.update(word.strip() for word in jieba.cut(part) if word.strip())
        else:
            result.add(part)
    return {word for word in result if len(word) > 1 or word.isdigit()}


def retrieve(
    organization_id: int,
    account_id: int,
    query: str,
    limit: int = 5,
) -> list[dict]:
    query_tokens = tokens(query)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, kind, title, question, answer, risk_level, source_ref,
                   valid_until, version
            FROM knowledge_unit
            WHERE organization_id=%s AND status='published'
              AND (valid_until IS NULL OR valid_until >= CURRENT_DATE)
              AND (
                EXISTS (
                  SELECT 1 FROM account a
                  JOIN access_role r ON r.id=a.role_id
                  WHERE a.id=%s AND 'data.all'=ANY(r.permissions)
                )
                OR scope='organization'
                OR (scope='team' AND team_id IN (
                  SELECT team_id FROM team_member WHERE account_id=%s
                ))
                OR (scope='private' AND owner_id=%s)
              )
            ORDER BY updated_at DESC
            LIMIT 500
            """,
            (organization_id, account_id, account_id, account_id),
        )
        rows = cur.fetchall()
    scored = []
    lowered_query = query.lower()
    for row in rows:
        haystack = " ".join(
            str(row.get(field) or "")
            for field in ("title", "question", "answer")
        )
        item_tokens = tokens(haystack)
        overlap = len(query_tokens & item_tokens)
        phrase = 3 if lowered_query and lowered_query in haystack.lower() else 0
        score = overlap * 2 + phrase
        if score:
            scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {**row, "score": score}
        for score, row in scored[:limit]
    ]

