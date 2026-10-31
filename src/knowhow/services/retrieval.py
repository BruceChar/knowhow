"""HNSW pgvector + Jieba lexical hybrid retrieval with weighted RRF."""
from __future__ import annotations

import os
import re

import jieba

from knowhow.db import connect
from knowhow.services.embeddings import (
    EmbeddingUnavailable,
    embed_text,
    model_name,
    pgvector_literal,
)

_WORD = re.compile(r"[a-zA-Z0-9][a-zA-Z0-9._-]*|[\u3400-\u9fff]+")
STOP_WORDS = {
    "a", "about", "an", "and", "are", "at", "be", "by", "can", "do",
    "does", "for", "from", "have", "how", "i", "in", "is", "it", "me",
    "my", "of", "on", "or", "our", "please", "the", "this", "to", "us",
    "we", "what", "when", "where", "which", "who", "why", "with", "you",
    "your",
}
RRF_K = 60
VECTOR_WEIGHT = 1.0
LEXICAL_WEIGHT = 0.5
VECTOR_MIN_SCORE = float(os.environ.get("KNOWHOW_VECTOR_MIN_SCORE", "0.35"))


def tokens(text: str) -> set[str]:
    result: set[str] = set()
    for part in _WORD.findall((text or "").lower()):
        if re.fullmatch(r"[\u3400-\u9fff]+", part):
            result.update(word.strip() for word in jieba.cut(part) if word.strip())
        else:
            result.add(part)
    return {
        word for word in result
        if (len(word) > 1 or word.isdigit()) and word not in STOP_WORDS
    }


def weighted_rrf(
    vector_rows: list[dict],
    lexical_rows: list[dict],
    limit: int,
) -> list[dict]:
    """Fuse independently ranked candidates while preserving source scores."""
    merged: dict[int, dict] = {}
    scores: dict[int, float] = {}
    for source, rows, weight in (
        ("vector", vector_rows, VECTOR_WEIGHT),
        ("lexical", lexical_rows, LEXICAL_WEIGHT),
    ):
        for rank, row in enumerate(rows, start=1):
            item_id = row["id"]
            merged.setdefault(item_id, dict(row))
            merged[item_id].setdefault("matched_by", [])
            if source not in merged[item_id]["matched_by"]:
                merged[item_id]["matched_by"].append(source)
            scores[item_id] = scores.get(item_id, 0.0) + weight / (RRF_K + rank)
    ranked = sorted(
        merged.values(),
        key=lambda row: (scores[row["id"]], row["id"]),
        reverse=True,
    )
    for row in ranked:
        row["rrf_score"] = round(scores[row["id"]], 8)
    return ranked[:limit]


def filter_vector_candidates(rows: list[dict]) -> list[dict]:
    """Drop nearest neighbors that are semantically unrelated to the query."""
    return [
        row for row in rows
        if float(row.get("vector_score") or 0.0) >= VECTOR_MIN_SCORE
    ]


def _lexical_candidates(
    organization_id: int,
    account_id: int,
    query: str,
) -> list[dict]:
    query_tokens = tokens(query)
    if not query_tokens:
        return []
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
            LIMIT 1000
            """,
            (organization_id, account_id, account_id, account_id),
        )
        rows = cur.fetchall()
    lowered = query.lower()
    scored = []
    for row in rows:
        haystack = " ".join(
            str(row.get(field) or "")
            for field in ("title", "question", "answer")
        )
        overlap = len(query_tokens & tokens(haystack))
        phrase_bonus = 3 if lowered and lowered in haystack.lower() else 0
        score = overlap * 2 + phrase_bonus
        if score:
            scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [
        {**row, "lexical_score": score}
        for score, row in scored[:24]
    ]


def _vector_candidates(
    organization_id: int,
    account_id: int,
    query: str,
    limit: int = 24,
) -> list[dict]:
    vector = pgvector_literal(embed_text(query))
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, kind, title, question, answer, risk_level, source_ref,
                   valid_until, version,
                   1-(embedding <=> %s::vector) AS vector_score
            FROM knowledge_unit
            WHERE organization_id=%s AND status='published'
              AND embedding IS NOT NULL
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
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (
                vector, organization_id, account_id, account_id, account_id,
                vector, limit,
            ),
        )
        return filter_vector_candidates(cur.fetchall())


def retrieve_with_meta(
    organization_id: int,
    account_id: int,
    query: str,
    limit: int = 5,
) -> tuple[list[dict], dict]:
    lexical = _lexical_candidates(organization_id, account_id, query)
    vector: list[dict] = []
    embedding_error = ""
    try:
        vector = _vector_candidates(organization_id, account_id, query)
    except EmbeddingUnavailable as exc:
        embedding_error = str(exc)
    if vector:
        mode = "hybrid"
    elif lexical:
        mode = "lexical"
    else:
        mode = "none"
    items = weighted_rrf(vector, lexical, limit)
    return items, {
        "mode": mode,
        "vector_candidates": len(vector),
        "lexical_candidates": len(lexical),
        "embedding_model": model_name(),
        "embedding_error": embedding_error,
        "vector_min_score": VECTOR_MIN_SCORE,
    }


def retrieve(
    organization_id: int,
    account_id: int,
    query: str,
    limit: int = 5,
) -> list[dict]:
    return retrieve_with_meta(
        organization_id, account_id, query, limit,
    )[0]

