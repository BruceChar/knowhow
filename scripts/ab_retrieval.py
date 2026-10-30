"""A/B 对比：纯向量检索 vs 混合检索（BM25+向量 RRF 融合）

在黄金集（57 条分层用例）上输出 Recall@5 / MRR@5，用于验证混合检索增益。
用法：HF_HUB_OFFLINE=1 uv run python scripts/ab_retrieval.py
"""
import json
import sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.core.search import hybrid_search, search_faq
from src.core.eval_runner import _hit, _ideal_list, _norm

DSN = "postgresql://rag:rag_dev_2026@localhost:5433/rag"
GOLDSET = Path(__file__).resolve().parent.parent / "data" / "goldset" / "jiacong_faq_goldset_v2.json"


def _run(mode: str, conn, cases: list[dict], top_k: int = 5):
    hits_n, mrr_sum = 0, 0.0
    per_case = []
    for c in cases:
        ideal = _ideal_list(c)
        if mode == "vector":
            res = search_faq(conn, c["query"], top_k)
        else:
            res = hybrid_search(conn, c["query"], top_k)
        qs = [r["question"] for r in res]
        hit = _hit(qs, ideal)
        rr = 0.0
        if hit:
            for i, q in enumerate(qs, start=1):
                if _norm(q) in {_norm(x) for x in ideal}:
                    rr = 1.0 / i
                    break
            hits_n += 1
        mrr_sum += rr
        per_case.append({"id": c["id"], "query": c["query"],
                         "hit": hit, "rr": rr})
    n = len(cases)
    return {
        "mode": mode,
        "recall_at_5": round(hits_n / n, 4),
        "mrr_at_5": round(mrr_sum / n, 4),
        "hits": hits_n, "cases": n,
        "misses": [p["id"] for p in per_case if not p["hit"]],
    }


def main():
    cases = json.load(open(GOLDSET, encoding="utf-8"))["cases"]
    print(f"黄金集: {len(cases)} 条 ({GOLDSET.name})")
    with psycopg.connect(DSN) as conn:
        vec = _run("vector", conn, cases)
        hyb = _run("hybrid", conn, cases)
    print(f"\n{'':>10}  Recall@5   MRR@5")
    print(f"{'纯向量':>10}  {vec['recall_at_5']:.4f}   {vec['mrr_at_5']:.4f}")
    print(f"{'混合BM25+向量':>10}  {hyb['recall_at_5']:.4f}   {hyb['mrr_at_5']:.4f}")
    print(f"\n混合召回增益: +{hyb['recall_at_5'] - vec['recall_at_5']:.4f} Recall, "
          f"+{hyb['mrr_at_5'] - vec['mrr_at_5']:.4f} MRR")
    print(f"\n纯向量 miss: {vec['misses']}")
    print(f"混合 miss  : {hyb['misses']}")


if __name__ == "__main__":
    main()
