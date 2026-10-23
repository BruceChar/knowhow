"""混合检索：关键词 + 向量 + 治理过滤，返回统一的知识卡结构。

防腐层驱动：embedding 通过 EmbeddingProvider（local bge-m3 / qwen3 / api 可切换），
query 统一走 embed_query（instruction 模型自动加前缀）。

用法（脚本/服务内）：
    from src.core.search import hybrid_search
    with psycopg.connect(DSN) as conn:
        hits = hybrid_search(conn, "MOQ是多少", k=5)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.providers.embedding_provider import get_embedding_provider


def embed_query(text: str, backend: str | None = None) -> list[float]:
    """防腐层查询编码（Qwen3 自动 instruction 前缀）。"""
    return get_embedding_provider(backend).embed_query(text)


def search_faq(conn, query: str, k: int = 5, permission: str = "internal",
               backend: str | None = None) -> list[dict]:
    """只检索 approved 且未过期、权限足够的 FAQ 知识卡（向量层）。"""
    qv = embed_query(query, backend)
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, question, answer, category, risk_level, action_level,
                   sources, 1 - (embedding <=> %s::vector) AS score
            FROM faq
            WHERE status = 'approved'
              AND (valid_from IS NULL OR valid_from <= CURRENT_DATE)
              AND (valid_until IS NULL OR valid_until >= CURRENT_DATE)
              AND permission = %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """, (qv, permission, qv, k))
        return [dict(zip(["id","question","answer","category","risk_level",
                          "action_level","sources","score"], r)) for r in cur.fetchall()]


def search_faq_keyword(conn, query: str, k: int = 5) -> list[dict]:
    """货号/型号类精确匹配——向量层对短代码召回差，先补关键词层。"""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, question, answer, category, risk_level, action_level, sources,
                   ts_rank(to_tsvector('simple', question || ' ' || answer), plainto_tsquery('simple', %s)) AS score
            FROM faq
            WHERE status = 'approved'
              AND (valid_from IS NULL OR valid_from <= CURRENT_DATE)
              AND (valid_until IS NULL OR valid_until >= CURRENT_DATE)
              AND (question ILIKE %s OR answer ILIKE %s OR category ILIKE %s)
            ORDER BY score DESC
            LIMIT %s
        """, (query, f"%{query}%", f"%{query}%", f"%{query}%", k))
        return [dict(zip(["id","question","answer","category","risk_level",
                          "action_level","sources","score"], r)) for r in cur.fetchall()]


def hybrid_search(conn, query: str, k: int = 5,
                  backend: str | None = None) -> list[dict]:
    """混合：向量召回 + 关键词召回 → 按分数融合去重，返回前 k 条知识卡。"""
    vec = search_faq(conn, query, k*2, backend=backend)
    kw  = search_faq_keyword(conn, query, k)
    merged: dict[int, dict] = {}
    for r in vec + kw:
        if r["id"] not in merged or r["score"] > merged[r["id"]]["score"]:
            merged[r["id"]] = r
    ranked = sorted(merged.values(), key=lambda x: x["score"], reverse=True)
    return ranked[:k]
