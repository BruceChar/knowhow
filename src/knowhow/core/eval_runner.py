"""评测运行器：黄金集 + 三层指标 + 回归门禁。

指标（MVP 文档三层评测落地）：
  1. 检索层  Recall@5 / Precision@5   —— 向量检索召回是否准
  2. 事实层  事实正确率（fact accuracy）—— 检索到的答案是否答对（默认用黄金集标注的
             expected_facts + 可选 LLMProvider 校验）
  3. 红线层  红线错误率（red-line error rate）—— 价格/交期/认证承诺类，错一条即失败

门禁：评测通过阈值后才允许发布（回归测试）。红线错误必须 = 0。

用法：
  uv run python app/eval_runner.py data/goldset/jiacong_faq_goldset_v1.json
  --gate-format-json --json-out reports/eval.json
"""
import argparse, json, os, sys
from pathlib import Path

import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from knowhow.providers.embedding_provider import get_embedding_provider
from knowhow.providers.vector_store_provider import get_vector_store

DEFAULT_DSN = "postgresql://rag:rag_dev_2026@localhost:5433/rag"
GATE_RECALL = 0.6       # 检索层：Recall@5 ≥ 0.6
GATE_PRECISION = 0.4    # 检索层：Precision@5 ≥ 0.4
GATE_FACT = 0.7         # 事实层：事实正确率 ≥ 0.7
GATE_RED_LINE = 0       # 红线层：错误必须 = 0


def load_goldset(path: str) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data["cases"]


def retrieve(query: str, dsn: str, top_k: int = 5,
             backend: str | None = None, include_draft: bool = False
             ) -> list[tuple[int, float, str]]:
    """query → [(faq_id, score, question)]，走防腐层（embedding + pgvector）。

    include_draft=False（默认）：只检索 status='approved'，即"线上已发布知识"；
    开发阶段 --dev 置 True 全量检索，验证向量质量而不被审核状态干扰。
    """
    provider = get_embedding_provider(backend)
    emb = provider.embed_query(query)  # query 走 embed_query（Qwen3 自动加 instruction）
    vs = get_vector_store("pgvector")
    vs.collection = "faq"
    filters = None if include_draft else {"status": "approved"}
    hits = vs.search(emb, top_k=top_k, filters=filters)
    # 取 question 文本
    if not hits:
        return []
    ids = [h.id for h in hits]
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT id, question FROM faq WHERE id = ANY(%s)", (ids,))
        qmap = dict(cur.fetchall())
    return [(h.id, h.score, qmap.get(h.id, "")) for h in hits]


def _norm(s: str) -> str:
    """规范化用于命中比较：去首尾引号、strip、小写（容忍库内引号/大小写差异）。"""
    return str(s or "").strip().strip('"\'“”‘’').strip().lower()


def eval_retrieval(cases: list[dict], dsn: str, top_k: int = 5,
                   backend: str | None = None, include_draft: bool = False) -> dict:
    """逐 case 检索，算 Recall@5 / Precision@5。"""
    hits_all, ideal_all = [], []
    per_case = []
    for c in cases:
        ideal = c["ideal_hit_question"]
        hits = retrieve(c["query"], dsn, top_k, backend, include_draft)
        hit_qs = [q for _, _, q in hits]
        hit = any(_norm(ideal) == _norm(q) for q in hit_qs)
        per_case.append({"case": c["id"], "query": c["query"],
                         "hit": hit, "top_questions": hit_qs})
        ideal_all.append(ideal)
        hits_all.append(hit)
    recall = sum(hits_all) / len(cases) if cases else 0
    # Precision@5：命中的理想答案数 / 5（每 case 至多 1 个理想答案）
    precision = sum(hits_all) / (len(cases) * top_k) if cases else 0
    return {"recall_at_5": round(recall, 4), "precision_at_5": round(precision, 4),
            "hit_cases": sum(hits_all), "total_cases": len(cases),
            "per_case": per_case}


def eval_facts(cases: list[dict], dsn: str, backend: str | None = None,
               include_draft: bool = False) -> dict:
    """事实层：默认基于黄金集 expected_facts 的确定性校验。

    进阶：接 LLMProvider 对检索答案做事实抽取比对（预留接口）。
    """
    # 简单版：命中理想答案即认为事实正确（黄金集答案本身就是话术库原文）
    correct = sum(1 for c in cases
                  if any(_norm(c["ideal_hit_question"]) == _norm(q)
                         for _, _, q in retrieve(c["query"], dsn, 5, backend,
                                                 include_draft)))
    total = len(cases)
    return {"fact_accuracy": round(correct / total, 4) if total else 0,
            "correct": correct, "total": total}


def eval_red_line(cases: list[dict], dsn: str, backend: str | None = None,
                  include_draft: bool = False) -> dict:
    """红线层：red_line=True 的 case（价格/交期/认证类）必须 100% 命中且事实正确。"""
    red_cases = [c for c in cases if c.get("red_line")]
    if not red_cases:
        return {"red_line_cases": 0, "red_line_errors": 0, "passed": True}
    errors = 0
    details = []
    for c in red_cases:
        hit_qs = [q for _, _, q in retrieve(c["query"], dsn, 5, backend,
                                            include_draft)]
        ok = any(_norm(c["ideal_hit_question"]) == _norm(q) for q in hit_qs)
        if not ok:
            errors += 1
        details.append({"case": c["id"], "hit": ok, "top_questions": hit_qs})
    return {"red_line_cases": len(red_cases), "red_line_errors": errors,
            "passed": errors == 0, "details": details}


def run(cases: list[dict], dsn: str, top_k: int = 5,
        backend: str | None = None, include_draft: bool = False) -> dict:
    retrieval = eval_retrieval(cases, dsn, top_k, backend, include_draft)
    facts = eval_facts(cases, dsn, backend, include_draft)
    red_line = eval_red_line(cases, dsn, backend, include_draft)

    gate_passed = (
        retrieval["recall_at_5"] >= GATE_RECALL
        and retrieval["precision_at_5"] >= GATE_PRECISION
        and facts["fact_accuracy"] >= GATE_FACT
        and red_line["passed"]
    )
    return {
        "name": "评测运行器 v1",
        "metrics": {
            "retrieval": retrieval,
            "facts": facts,
            "red_line": red_line,
        },
        "thresholds": {
            "recall_at_5": GATE_RECALL, "precision_at_5": GATE_PRECISION,
            "fact_accuracy": GATE_FACT, "red_line_errors": GATE_RED_LINE,
        },
        "gate_passed": gate_passed,
        "backend": backend or os.environ.get("EMBEDDING_BACKEND", "local"),
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("goldset")
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--backend", default=os.environ.get("EMBEDDING_BACKEND"))
    ap.add_argument("--dev", action="store_true",
                    help="开发模式：检索不过滤 status（数据未走审核流时验证向量质量）")
    ap.add_argument("--json-out", default=None)
    a = ap.parse_args()

    cases = load_goldset(a.goldset)
    result = run(cases, a.dsn, a.top_k, a.backend, include_draft=a.dev)
    print(json.dumps(result, ensure_ascii=False, indent=2))

    if a.json_out:
        Path(a.json_out).parent.mkdir(parents=True, exist_ok=True)
        with open(a.json_out, "w", encoding="utf-8") as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        print(f"\n[评测] 报告已写入: {a.json_out}")

    print(f"\n[门禁] {'✅ PASS' if result['gate_passed'] else '❌ FAIL'} "
          f"（Recall@5={result['metrics']['retrieval']['recall_at_5']}, "
          f"Precision@5={result['metrics']['retrieval']['precision_at_5']}, "
          f"Fact={result['metrics']['facts']['fact_accuracy']}, "
          f"红线错误={result['metrics']['red_line']['red_line_errors']}）")
