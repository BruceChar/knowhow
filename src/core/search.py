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

RRF_K = 60.0


def _rrf(ranks: list[int]) -> float:
    return sum(1.0 / (RRF_K + rank) for rank in ranks)


def embed_query(text: str, backend: str | None = None) -> list[float]:
    """防腐层查询编码（Qwen3 自动 instruction 前缀）。"""
    return get_embedding_provider(backend).embed_query(text)


def search_faq(conn, query: str, k: int = 5,
               permissions: list[str] | None = None,
               customer_ready: bool | None = None,
               backend: str | None = None) -> list[dict]:
    """只检索 approved 且未过期、权限足够的 FAQ 知识卡（向量层）。"""
    qv = embed_query(query, backend)
    permissions = permissions or ["public", "internal"]
    extra = ""
    params: list = [qv, permissions]
    if customer_ready is not None:
        extra = " AND customer_ready = %s"
        params.append(customer_ready)
    params.extend([qv, k])
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT id, question, answer, category, risk_level, action_level,
                   sources, permission, customer_ready,
                   1 - (embedding <=> %s::vector) AS vector_score
            FROM active_faq
            WHERE (valid_from IS NULL OR valid_from <= CURRENT_DATE)
              AND (valid_until IS NULL OR valid_until >= CURRENT_DATE)
              AND permission = ANY(%s::text[])
              AND embedding IS NOT NULL
              {extra}
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """, params)
        cols = ["id", "question", "answer", "category", "risk_level",
                "action_level", "sources", "permission", "customer_ready",
                "vector_score"]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def search_faq_keyword(conn, query: str, k: int = 5,
                       permissions: list[str] | None = None,
                       customer_ready: bool | None = None) -> list[dict]:
    """货号/型号类精确匹配——向量层对短代码召回差，先补关键词层。"""
    permissions = permissions or ["public", "internal"]
    extra = ""
    params: list = [query, permissions]
    if customer_ready is not None:
        extra = " AND customer_ready = %s"
        params.append(customer_ready)
    params.extend([f"%{query}%", f"%{query}%", f"%{query}%", k])
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT id, question, answer, category, risk_level, action_level,
                   sources, permission, customer_ready,
                   ts_rank(to_tsvector('simple', question || ' ' || answer),
                           plainto_tsquery('simple', %s)) AS keyword_score
            FROM active_faq
            WHERE (valid_from IS NULL OR valid_from <= CURRENT_DATE)
              AND (valid_until IS NULL OR valid_until >= CURRENT_DATE)
              AND permission = ANY(%s::text[])
              AND embedding IS NOT NULL
              {extra}
              AND (question ILIKE %s OR answer ILIKE %s OR category ILIKE %s)
            ORDER BY keyword_score DESC
            LIMIT %s
        """, params)
        cols = ["id", "question", "answer", "category", "risk_level",
                "action_level", "sources", "permission", "customer_ready",
                "keyword_score"]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def hybrid_search(conn, query: str, k: int = 5,
                  backend: str | None = None,
                  permissions: list[str] | None = None,
                  customer_ready: bool | None = None) -> list[dict]:
    """混合同一路径过滤后的两路候选，并用 RRF 合并排名。"""
    candidate_k = max(k * 4, 20)
    vec = search_faq(conn, query, candidate_k, permissions,
                     customer_ready, backend=backend)
    kw = search_faq_keyword(conn, query, candidate_k, permissions,
                            customer_ready)
    merged: dict[int, dict] = {}
    ranks: dict[int, list[int]] = {}
    for source, results in (("vector", vec), ("keyword", kw)):
        for rank, row in enumerate(results, start=1):
            item_id = row["id"]
            merged.setdefault(item_id, row)
            ranks.setdefault(item_id, []).append(rank)
            merged[item_id].setdefault("matched_by", []).append(source)
    for item_id, row in merged.items():
        row["retrieval_score"] = _rrf(ranks[item_id])
    ranked = sorted(merged.values(),
                    key=lambda item: item["retrieval_score"], reverse=True)
    return ranked[:k]
