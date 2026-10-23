"""管理台 API：审核工作流 + 打标 + RBAC 权限。

启动：
  uv run uvicorn app.admin_api:app --port 8000

角色测试（X-User-Id 头）：
  curl -H "X-User-Id: u_admin" http://localhost:8000/faq?status=draft
  curl -X POST -H "X-User-Id: u_reviewer" -H "Content-Type: application/json" \
    http://localhost:8000/faq/1/approve

数据级权限：agent（客服）只读 public+internal；confidential 需 editor 以上。
"""
from __future__ import annotations

import os

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.auth.rbac import User, data_permission_clause, get_current_user, require_role

DSN = os.environ.get("RAG_DSN", "postgresql://rag:rag_dev_2026@localhost:5433/rag")
app = FastAPI(title="企业知识库管理台", version="0.1")

# 静态资源：评测审计面板
import pathlib
_static_dir = pathlib.Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/admin", include_in_schema=False)
def admin_panel():
    """后台管理端评测审计面板（单页 HTML）。"""
    from fastapi.responses import FileResponse
    return FileResponse(str(_static_dir / "admin.html"))

# 模型预热：服务启动时加载 embedding 模型，评测/检索复用，避免每次冷启动 9s+
_embedding_provider = None


@app.on_event("startup")
def _warmup():
    global _embedding_provider
    try:
        from src.providers.embedding_provider import get_embedding_provider
        _embedding_provider = get_embedding_provider()
        # 预热：编码一条空文本触发真实加载
        _embedding_provider.embed(["warmup"])
    except Exception as e:  # 模型加载失败不阻塞启动（评测时再报）
        print(f"[warmup] embedding 预热失败（评测时重试）: {e}")


def _conn():
    return psycopg.connect(DSN)


# ============ 模型 ============

class FAQUpdate(BaseModel):
    question: str | None = None
    answer: str | None = None
    category: str | None = None
    tags: list[str] | None = None
    risk_level: str | None = None
    action_level: str | None = None
    permission: str | None = None


class ReviewAction(BaseModel):
    comment: str | None = None


# ============ FAQ 查询（含数据级权限过滤） ============

@app.get("/faq")
def list_faq(status: str | None = Query(default=None),
             category: str | None = None,
             q: str | None = None,
             user: User = Depends(get_current_user)):
    """客服/业务员查询 FAQ；数据级：按角色过滤 permission。"""
    where = [data_permission_clause(user, "f")]
    params: list = []
    if status:
        where.append("f.status = %s"); params.append(status)
    if category:
        where.append("f.category = %s"); params.append(category)
    if q:
        where.append("f.question ILIKE %s"); params.append(f"%{q}%")
    sql = f"""
        SELECT id, question, answer, category, tags, status,
               risk_level, action_level, permission, version
        FROM faq f WHERE {' AND '.join(where)}
        ORDER BY f.id LIMIT 100
    """
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    cols = ["id", "question", "answer", "category", "tags", "status",
            "risk_level", "action_level", "permission", "version"]
    return [dict(zip(cols, r)) for r in rows]


@app.get("/faq/{faq_id}")
def get_faq(faq_id: int, user: User = Depends(get_current_user)):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"""
            SELECT id, question, answer, category, tags, sources, status,
                   valid_from, valid_until, permission, risk_level,
                   action_level, version, approved_by, approved_at, updated_at
            FROM faq WHERE id=%s AND {data_permission_clause(user, 'faq')}
        """, (faq_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "FAQ 不存在或无权查看")
    cols = ["id", "question", "answer", "category", "tags", "sources", "status",
            "valid_from", "valid_until", "permission", "risk_level",
            "action_level", "version", "approved_by", "approved_at", "updated_at"]
    return dict(zip(cols, row))


# ============ 编辑（业务员以上） ============

@app.put("/faq/{faq_id}")
def update_faq(faq_id: int, body: FAQUpdate,
               user: User = Depends(require_role("editor"))):
    """编辑知识卡；改字段 → version+1（历史可追溯）。"""
    fields, params = [], []
    for k, v in body.model_dump(exclude_none=True).items():
        fields.append(f"{k} = %s")
        params.append(v if not isinstance(v, list) else v)  # tags 数组直接传
    if not fields:
        raise HTTPException(400, "无更新字段")
    fields.append("version = version + 1")
    fields.append("status = 'draft'")  # 改过即回到待审核
    params.append(faq_id)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE faq SET {', '.join(fields)} WHERE id=%s "
                    f"AND {data_permission_clause(user, 'faq')}", params)
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "FAQ 不存在或无权修改")
    return {"ok": True, "id": faq_id, "version_bumped": True}


# ============ 打标（业务员以上） ============

@app.post("/faq/{faq_id}/tags")
def set_tags(faq_id: int, body: FAQUpdate,
             user: User = Depends(require_role("editor"))):
    """5 维打标：category / tags / risk_level / action_level / permission。"""
    upd, params = [], []
    for col in ("category", "tags", "risk_level", "action_level", "permission"):
        v = getattr(body, col)
        if v is not None:
            upd.append(f"{col} = %s")
            params.append(v)
    if not upd:
        raise HTTPException(400, "至少提供一维标签")
    params.append(faq_id)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE faq SET {', '.join(upd)} WHERE id=%s "
                    f"AND {data_permission_clause(user, 'faq')}", params)
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "FAQ 不存在或无权打标")
    return {"ok": True, "id": faq_id, "tagged": [u.split(' = ')[0] for u in upd]}


# ============ 审核工作流（审核员以上） ============

@app.post("/faq/{faq_id}/approve")
def approve_faq(faq_id: int, body: ReviewAction | None = None,
                user: User = Depends(require_role("reviewer"))):
    """审核通过：draft → approved，记录审批人。"""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE faq SET status='approved', approved_by=%s, approved_at=now()
            WHERE id=%s AND status='draft'
        """, (user.name, faq_id))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(409, "仅 draft 状态可审核通过")
    return {"ok": True, "id": faq_id, "status": "approved", "by": user.name}


@app.post("/faq/{faq_id}/reject")
def reject_faq(faq_id: int, body: ReviewAction | None = None,
               user: User = Depends(require_role("reviewer"))):
    """审核驳回：draft → rejected（保持可编辑）。"""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE faq SET status='deprecated', approved_by=%s, "
                    "approved_at=now() WHERE id=%s", (user.name, faq_id))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "FAQ 不存在")
    return {"ok": True, "id": faq_id, "status": "deprecated"}


# ============ 入录批次查询（管理员） ============

@app.get("/batches")
def list_batches(user: User = Depends(require_role("reviewer"))):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, source_file, source_kind, total_rows, loaded_rows,
                   pending_rows, bad_rows, status, started_at, finished_at
            FROM import_batch ORDER BY id DESC LIMIT 50
        """)
        rows = cur.fetchall()
    cols = ["id", "source_file", "source_kind", "total_rows", "loaded_rows",
            "pending_rows", "bad_rows", "status", "started_at", "finished_at"]
    return [dict(zip(cols, r)) for r in rows]


@app.get("/health")
def health():
    return {"status": "ok", "service": "admin-api"}


# ============ 评测与召回审计（reviewer 以上） ============

GOLDSET_PATH = os.environ.get("GOLDSET_PATH",
                              "data/goldset/jiacong_faq_goldset_v2.json")


@app.post("/eval/run")
def run_eval(dev: bool = True,
             user: User = Depends(require_role("reviewer"))):
    """触发一次评测（默认 dev 模式：检索不过滤审核状态），结果持久化 eval_report。"""
    from src.core import eval_runner
    cases = eval_runner.load_goldset(GOLDSET_PATH)
    result = eval_runner.run(cases, DSN, include_draft=dev)
    rid = eval_runner.save_report(result, GOLDSET_PATH, DSN)
    return {"report_id": rid, "gate_passed": result["gate_passed"],
            "metrics": {k: v for k, v in result["metrics"].items()
                        if k != "retrieval"},
            "retrieval": {k: v for k, v in result["metrics"]["retrieval"].items()
                          if k != "per_case"}}


@app.get("/eval/reports")
def list_eval_reports(limit: int = Query(default=20, le=100),
                      user: User = Depends(require_role("reviewer"))):
    """评测历史（趋势看板数据）。"""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, goldset_name, backend, recall_at_5, precision_at_5,
                   mrr_at_5, fact_accuracy, red_line_errors, gate_passed,
                   total_cases, hit_cases, created_at
            FROM eval_report ORDER BY id DESC LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
    cols = ["id", "goldset_name", "backend", "recall_at_5", "precision_at_5",
            "mrr_at_5", "fact_accuracy", "red_line_errors", "gate_passed",
            "total_cases", "hit_cases", "created_at"]
    return [dict(zip(cols, r)) for r in rows]


@app.get("/eval/reports/{report_id}")
def get_eval_report(report_id: int,
                    user: User = Depends(require_role("reviewer"))):
    """报告明细：含 per-case 召回审计（query/hit/top5/rr）与红线详情。"""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, goldset_name, backend, recall_at_5, precision_at_5,
                   mrr_at_5, fact_accuracy, red_line_errors, gate_passed,
                   total_cases, hit_cases, detail_json, created_at
            FROM eval_report WHERE id=%s
        """, (report_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "评测报告不存在")
    cols = ["id", "goldset_name", "backend", "recall_at_5", "precision_at_5",
            "mrr_at_5", "fact_accuracy", "red_line_errors", "gate_passed",
            "total_cases", "hit_cases", "detail_json", "created_at"]
    d = dict(zip(cols, row))
    d["detail"] = d.pop("detail_json")
    return d


@app.get("/audit-log")
def list_audit_log(limit: int = Query(default=50, le=200),
                   decision: str | None = None,
                   user: User = Depends(require_role("reviewer"))):
    """问答审计：每次客服交互的检索决策、引用、耗时。"""
    where, params = [], []
    if decision:
        where.append("decision = %s"); params.append(decision)
    sql = f"""
        SELECT inquiry_id, channel, question, answer, citations, action_level,
               decision, latency_ms, model, created_at
        FROM audit_log {'WHERE ' + ' AND '.join(where) if where else ''}
        ORDER BY id DESC LIMIT %s
    """
    params.append(limit)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    cols = ["inquiry_id", "channel", "question", "answer", "citations",
            "action_level", "decision", "latency_ms", "model", "created_at"]
    return [dict(zip(cols, r)) for r in rows]
