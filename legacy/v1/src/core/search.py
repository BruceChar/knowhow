"""混合检索：BM25 关键词 + 向量 + 治理过滤，返回统一的知识卡结构。

防腐层驱动：embedding 通过 EmbeddingProvider（local bge-m3 / qwen3 / api 可切换），
query 统一走 embed_query（instruction 模型自动加前缀）。

两路召回在"同一 SQL 治理过滤后的候选集"上各取 TopN，再用 RRF 合并排名：
- 向量路：pgvector 余弦相似度（对同义改写/语义相似强）
- BM25 路：jieba 中文分词 + 经典 BM25（对货号/型号/词面命中的问句强）
关键词层此前用 PG ts_rank('simple')——'simple' 不做中文分词，中文问句实际失效；
现改为 jieba 分词 + Python BM25，中英文/货号都有效。

用法（脚本/服务内）：
    from src.core.search import hybrid_search
    with psycopg.connect(DSN) as conn:
        hits = hybrid_search(conn, "MOQ是多少", k=5)
"""
from __future__ import annotations

import math
import os
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.providers.embedding_provider import get_embedding_provider

RRF_K = 60.0
# 加权 RRF：向量语义为主导、BM25 词面作辅助（两路异质，弱 BM25 不能挤掉向量强项）
W_VECTOR = 1.0
W_BM25 = 0.5
BM25_K1 = 1.5
BM25_B = 0.75
# BM25 路候选上限：过滤后按词命中预筛，再精排（MVP 数据量 2k+ 级）
_BM25_CANDIDATE_LIMIT = 500

_jieba_ready = False


def _ensure_jieba():
    global _jieba_ready
    if not _jieba_ready:
        import jieba
        jieba.initialize()
        _jieba_ready = True


def _tokens(text) -> list[str]:
    """jieba 分词 + 小写 + 去空白；英文货号/型号会被切成独立 token。"""
    _ensure_jieba()
    import jieba
    return [t.strip().lower() for t in jieba.cut(str(text or "")) if t.strip()]


def _bm25_idf(n_docs: int, df: int) -> float:
    return math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)


def _bm25_score(query_tokens: list[str], doc_tokens: list[str],
                idf: dict[str, float], avgdl: float) -> float:
    """经典 BM25：k1=1.5, b=0.75。"""
    if not doc_tokens:
        return 0.0
    dl = len(doc_tokens)
    counts: dict[str, int] = {}
    for t in doc_tokens:
        counts[t] = counts.get(t, 0) + 1
    score = 0.0
    for t in query_tokens:
        tf = counts.get(t, 0)
        if tf == 0 or t not in idf:
            continue
        denom = tf + BM25_K1 * (1 - BM25_B + BM25_B * dl / avgdl)
        score += idf[t] * (tf * (BM25_K1 + 1)) / denom
    return score


def _rrf(ranks: list[int]) -> float:
    return sum(1.0 / (RRF_K + rank) for rank in ranks)


def _rrf_weighted(rank_pairs: list[tuple[str, int]]) -> float:
    """加权 RRF：vector 权重 1.0、bm25 权重 0.5。"""
    score = 0.0
    for source, rank in rank_pairs:
        w = W_VECTOR if source == "vector" else W_BM25
        score += w / (RRF_K + rank)
    return score


def embed_query(text: str, backend: str | None = None) -> list[float]:
    """防腐层查询编码（Qwen3 自动 instruction 前缀）。"""
    return get_embedding_provider(backend).embed_query(text)


def _base_where(permissions: list[str] | None,
                customer_ready: bool | None) -> tuple[str, list]:
    """治理过滤统一前置：仅 approved 且未过期、权限可见。"""
    perms = permissions or ["public", "internal"]
    where = ("(valid_from IS NULL OR valid_from <= CURRENT_DATE)"
             " AND (valid_until IS NULL OR valid_until >= CURRENT_DATE)"
             " AND permission = ANY(%s::text[])")
    params: list = [perms]
    if customer_ready is not None:
        where += " AND customer_ready = %s"
        params.append(customer_ready)
    return where, params


def search_faq(conn, query: str, k: int = 5,
               permissions: list[str] | None = None,
               customer_ready: bool | None = None,
               backend: str | None = None) -> list[dict]:
    """向量路：只检索 approved 且未过期、权限足够的 FAQ（pgvector 余弦）。"""
    qv = embed_query(query, backend)
    where, params = _base_where(permissions, customer_ready)
    # SQL 参数顺序：vector 在最前（SELECT 内）、随后过滤、ORDER 内 vector、LIMIT
    params = [qv, *params, qv, k]
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT id, question, answer, category, risk_level, action_level,
                   sources, permission, customer_ready,
                   1 - (embedding <=> %s::vector) AS vector_score
            FROM active_faq
            WHERE {where}
              AND embedding IS NOT NULL
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """, params)
        cols = ["id", "question", "answer", "category", "risk_level",
                "action_level", "sources", "permission", "customer_ready",
                "vector_score"]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def search_faq_bm25(conn, query: str, k: int = 5,
                    permissions: list[str] | None = None,
                    customer_ready: bool | None = None) -> list[dict]:
    """BM25 路：jieba 中文分词 + BM25 精排。

    候选 = 治理过滤后、且至少命中一个 query 词的 FAQ；在候选语料上统计
    df/avgdl 后对 query 打分取 TopK。货号/型号（如 PS-2000）与词面命中的
    中文问句由本路负责，与向量路互补。
    """
    q_tokens = _tokens(query)
    if not q_tokens:
        return []
    where, params = _base_where(permissions, customer_ready)
    # 词命中预筛：任一 query 词命中 question/answer/category 即入候选
    like_conds = " OR ".join(
        ["(question ILIKE %s OR answer ILIKE %s OR category ILIKE %s)"
         for _ in q_tokens])
    or_params = [f"%{t}%" for t in q_tokens for _ in range(3)]
    params.extend(or_params)
    params.append(_BM25_CANDIDATE_LIMIT)
    with conn.cursor() as cur:
        cur.execute(f"""
            SELECT id, question, answer, category, risk_level, action_level,
                   sources, permission, customer_ready
            FROM active_faq
            WHERE {where}
              AND ({like_conds})
            LIMIT %s
        """, params)
        cols = ["id", "question", "answer", "category", "risk_level",
                "action_level", "sources", "permission", "customer_ready"]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    if not rows:
        return []
    # 语料统计 + 打分
    docs = [_tokens(r["question"] + " " + r["answer"] + " " + r["category"])
            for r in rows]
    n = len(rows)
    avgdl = sum(len(d) for d in docs) / n if n else 0.0
    df: dict[str, int] = {}
    for d in docs:
        for t in set(d):
            df[t] = df.get(t, 0) + 1
    idf = {t: _bm25_idf(n, df[t]) for t in q_tokens if t in df}
    scored = []
    for r, doc_tokens in zip(rows, docs):
        s = _bm25_score(q_tokens, doc_tokens, idf, avgdl)
        if s > 0:
            r["keyword_score"] = s
            scored.append(r)
    if not scored:
        return []
    # 质量门槛：仅保留相对强词面命中（>= top 得分的 30%），
    # 抑制弱匹配项在 RRF 融合中挤掉向量路强项（如价格类长问句）
    scored.sort(key=lambda r: r["keyword_score"], reverse=True)
    top = scored[0]["keyword_score"]
    scored = [r for r in scored if r["keyword_score"] >= top * 0.3]
    return scored[:k]


def hybrid_search(conn, query: str, k: int = 5,
                  backend: str | None = None,
                  permissions: list[str] | None = None,
                  customer_ready: bool | None = None) -> list[dict]:
    """混合同一路径过滤后的两路候选，并用加权 RRF 合并排名。

    向量路候选放宽（4k，语义召回宽）；BM25 路精取（k，只保留强词面命中）
    —— 防止 BM25 弱匹配项叠加后挤掉向量路边缘命中的理想答案。
    """
    candidate_k = max(k * 4, 20)
    vec = search_faq(conn, query, candidate_k, permissions,
                     customer_ready, backend=backend)
    kw = search_faq_bm25(conn, query, k, permissions,
                         customer_ready)
    merged: dict[int, dict] = {}
    ranks: dict[int, list[tuple[str, int]]] = {}
    for source, results in (("vector", vec), ("bm25", kw)):
        for rank, row in enumerate(results, start=1):
            item_id = row["id"]
            merged.setdefault(item_id, row)
            ranks.setdefault(item_id, []).append((source, rank))
            merged[item_id].setdefault("matched_by", []).append(source)
    for item_id, row in merged.items():
        row["retrieval_score"] = _rrf_weighted(ranks[item_id])
    ranked = sorted(merged.values(),
                    key=lambda item: item["retrieval_score"], reverse=True)
    return ranked[:k]
