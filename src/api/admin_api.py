"""Unified FastAPI application for admin, knowledge Q&A and import."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from urllib.parse import urlparse

import psycopg
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.auth.rbac import (
    User,
    data_permission_clause,
    get_current_user,
    require_permission,
)
from src.config import RAG_DSN as DSN, MAX_UPLOAD_MB
from src.config import SESSION_TTL_HOURS, LOGIN_LOCK_MINUTES
from fastapi.responses import JSONResponse
app = FastAPI(title="企业知识库管理台", version="0.1")

# 静态资源：评测审计面板
import pathlib
_static_dir = pathlib.Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.get("/", include_in_schema=False)
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/admin")


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    """Reject cross-origin state changes that ride on a cookie session."""
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        has_cookie_session = bool(request.cookies.get("kh_session"))
        if has_cookie_session and request.url.path != "/auth/login":
            expected = request.url.netloc
            origin = request.headers.get("origin", "")
            csrf_header = request.headers.get("x-knowhow-csrf", "")
            if csrf_header != "1":
                return JSONResponse({"detail": "缺少 CSRF 校验头"}, status_code=403)
            if origin and urlparse(origin).netloc != expected:
                return JSONResponse({"detail": "拒绝跨站请求"}, status_code=403)
    return await call_next(request)


@app.get("/admin", include_in_schema=False)
def admin_panel():
    """后台管理端评测审计面板（单页 HTML）。"""
    from fastapi.responses import FileResponse
    return FileResponse(str(_static_dir / "admin.html"))


@app.get("/agent", include_in_schema=False)
def customer_service_agent():
    """客服辅助入口；与后台共用认证和数据权限。"""
    from fastapi.responses import FileResponse
    return FileResponse(str(_static_dir / "admin.html"))

# 模型预热：服务启动时加载 embedding 模型，评测/检索复用，避免每次冷启动 9s+
_embedding_provider = None


@app.on_event("startup")
def _warmup():
    global _embedding_provider
    # 种子用户（幂等；演示默认密码，生产必须修改）
    try:
        from src.auth import auth as _auth
        _auth.seed_users(DSN)
    except Exception as e:
        print(f"[startup] 种子用户失败: {e}")
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
    customer_ready: bool | None = None


class ReviewAction(BaseModel):
    comment: str | None = None


class OfferReviewAction(BaseModel):
    comment: str | None = None


class AskBody(BaseModel):
    question: str
    channel: str = "web"
    customer_id: str = ""
    sku_code: str = ""


class FeedbackBody(BaseModel):
    inquiry_id: str
    verdict: str
    reason: str = ""
    corrected_answer: str = ""


# ============ 知识问答（受认证、可见域和 customer_ready 约束） ============

@app.post("/v1/ask")
def ask(body: AskBody, user: User = Depends(get_current_user)):
    from dataclasses import asdict
    from src.core.knowledge import ask_question
    with _conn() as conn:
        result = ask_question(
            conn, user, body.question.strip(), channel=body.channel,
            customer_id=body.customer_id, sku_code=body.sku_code,
            fact_dsn=DSN,
        )
    return asdict(result)


@app.post("/v1/inquiry")
def inquiry_compat(body: AskBody, user: User = Depends(get_current_user)):
    """Compatibility alias for clients still calling the tutorial endpoint."""
    return ask(body, user)


@app.post("/api/v1/feedback")
def submit_feedback(body: FeedbackBody,
                    user: User = Depends(require_permission("agent.use"))):
    """Record operator judgement against one immutable Q&A audit record."""
    if body.verdict not in {"correct", "incorrect", "needs_review"}:
        raise HTTPException(400, "verdict 必须是 correct/incorrect/needs_review")
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, citations FROM audit_log
            WHERE inquiry_id=%s
        """, (body.inquiry_id,))
        audit = cur.fetchone()
        if not audit:
            raise HTTPException(404, "问答记录不存在")
        audit_id, citations = audit
        feedback_text = json.dumps({
            "verdict": body.verdict,
            "reason": body.reason,
            "by": user.name,
        }, ensure_ascii=False)
        cur.execute(
            "UPDATE audit_log SET user_feedback=%s WHERE id=%s",
            (feedback_text, audit_id),
        )
        issue_id = None
        if body.verdict != "correct":
            faq_id = None
            if citations:
                faq_id = int(citations[0]) if isinstance(citations, list) and citations else None
            cur.execute("""
                INSERT INTO issue
                  (faq_id, inquiry_id, feedback, resolution, status,
                   created_by, related_audit)
                VALUES (%s,%s,%s,%s,'open',%s,%s)
                RETURNING id
            """, (
                faq_id, body.inquiry_id, feedback_text,
                body.corrected_answer, user.name, audit_id,
            ))
            issue_id = cur.fetchone()[0]
        conn.commit()
    return {"ok": True, "inquiry_id": body.inquiry_id,
            "issue_id": issue_id, "verdict": body.verdict}


@app.get("/api/v1/feedback/issues")
def list_feedback_issues(status: str | None = "open",
                         user: User = Depends(require_permission("audit.view"))):
    where, params = [], []
    if status:
        where.append("i.status=%s"); params.append(status)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"""
            SELECT i.id, i.inquiry_id, i.feedback, i.resolution, i.status,
                   i.created_by, i.created_at, a.question, a.answer,
                   a.citations
            FROM issue i
            LEFT JOIN audit_log a ON a.id=i.related_audit
            {"WHERE " + " AND ".join(where) if where else ""}
            ORDER BY i.id DESC LIMIT 200
        """, params)
        rows = cur.fetchall()
    cols = ["id", "inquiry_id", "feedback", "resolution", "status",
            "created_by", "created_at", "question", "answer", "citations"]
    return [dict(zip(cols, row)) for row in rows]


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
               risk_level, action_level, permission, customer_ready,
               version, published_version
        FROM faq f WHERE {' AND '.join(where)}
        ORDER BY f.id LIMIT 100
    """
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    cols = ["id", "question", "answer", "category", "tags", "status",
            "risk_level", "action_level", "permission", "customer_ready",
            "version", "published_version"]
    return [dict(zip(cols, r)) for r in rows]


@app.get("/faq/{faq_id}")
def get_faq(faq_id: int, user: User = Depends(get_current_user)):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(f"""
            SELECT id, question, answer, category, tags, sources, status,
                   valid_from, valid_until, permission, risk_level,
                   action_level, customer_ready, version, published_version,
                   approved_by, approved_at, updated_at
            FROM faq WHERE id=%s AND {data_permission_clause(user, 'faq')}
        """, (faq_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "FAQ 不存在或无权查看")
    cols = ["id", "question", "answer", "category", "tags", "sources", "status",
            "valid_from", "valid_until", "permission", "risk_level",
            "action_level", "customer_ready", "version", "published_version",
            "approved_by", "approved_at", "updated_at"]
    return dict(zip(cols, row))


# ============ 编辑（业务员以上） ============

@app.put("/faq/{faq_id}")
def update_faq(faq_id: int, body: FAQUpdate,
               user: User = Depends(require_permission("faq.edit"))):
    """Create a candidate edit while retaining the last published snapshot."""
    fields, params = [], []
    for k, v in body.model_dump(exclude_none=True).items():
        fields.append(f"{k} = %s")
        params.append(v if not isinstance(v, list) else v)  # tags 数组直接传
    if not fields:
        raise HTTPException(400, "无更新字段")
    fields.append("version = version + 1")
    fields.append("status = 'draft'")
    if body.question is not None or body.answer is not None:
        fields.append("embedding = NULL")
    params.append(faq_id)
    with _conn() as conn, conn.cursor() as cur:
        # Preserve the last published version before an approved row becomes
        # a review candidate.
        cur.execute("""
            INSERT INTO faq_version
              (faq_id, version, snapshot, status, created_by, approved_by,
               approved_at)
            SELECT id, version,
                   to_jsonb(f) - 'embedding' - 'published_embedding'
                     - 'published_snapshot',
                   'superseded', approved_by, approved_by, approved_at
            FROM faq f
            WHERE id=%s AND status='approved'
            ON CONFLICT (faq_id, version) DO NOTHING
        """, (faq_id,))
        cur.execute(f"UPDATE faq SET {', '.join(fields)} WHERE id=%s "
                    f"AND {data_permission_clause(user, 'faq')}", params)
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "FAQ 不存在或无权修改")
    return {"ok": True, "id": faq_id, "version_bumped": True}


# ============ 打标（业务员以上） ============

@app.post("/faq/{faq_id}/tags")
def set_tags(faq_id: int, body: FAQUpdate,
             user: User = Depends(require_permission("faq.edit"))):
    """5 维打标：category / tags / risk_level / action_level / permission。"""
    upd, params = [], []
    for col in ("category", "tags", "risk_level", "action_level", "permission",
                "customer_ready"):
        v = getattr(body, col)
        if v is not None:
            upd.append(f"{col} = %s")
            params.append(v)
    if not upd:
        raise HTTPException(400, "至少提供一维标签")
    params.append(faq_id)
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO faq_version
              (faq_id, version, snapshot, status, created_by, approved_by,
               approved_at)
            SELECT id, version,
                   to_jsonb(f) - 'embedding' - 'published_embedding'
                     - 'published_snapshot',
                   'superseded', approved_by, approved_by, approved_at
            FROM faq f
            WHERE id=%s AND status='approved'
            ON CONFLICT (faq_id, version) DO NOTHING
        """, (faq_id,))
        cur.execute(f"UPDATE faq SET {', '.join(upd)}, status='draft' WHERE id=%s "
                    f"AND {data_permission_clause(user, 'faq')}", params)
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(404, "FAQ 不存在或无权打标")
    return {"ok": True, "id": faq_id, "tagged": [u.split(' = ')[0] for u in upd]}


# ============ 产品事实审核（发布后才能进入实时事实工具） ============

@app.get("/products")
def list_products(status: str | None = None,
                  q: str | None = None,
                  user: User = Depends(require_permission("faq.view"))):
    where, params = [], []
    if status:
        where.append("s.status=%s"); params.append(status)
    if q:
        where.append("(s.sku_code ILIKE %s OR s.name_zh ILIKE %s "
                     "OR s.name_en ILIKE %s)")
        params.extend([f"%{q}%"] * 3)
    sql = f"""
        SELECT s.id, s.sku_code, s.category, s.name_zh, s.name_en,
               s.status, s.version, s.valid_until,
               count(DISTINCT v.id) AS variant_count,
               count(DISTINCT o.id) FILTER (
                 WHERE o.status='approved' AND o.valid_until >= CURRENT_DATE
               ) AS active_offer_count
        FROM sku s
        LEFT JOIN variant v ON v.sku_id=s.id
        LEFT JOIN offer o ON o.variant_id=v.id
        {"WHERE " + " AND ".join(where) if where else ""}
        GROUP BY s.id
        ORDER BY s.id DESC LIMIT 200
    """
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    cols = ["id", "sku_code", "category", "name_zh", "name_en", "status",
            "version", "valid_until", "variant_count", "active_offer_count"]
    return [dict(zip(cols, row)) for row in rows]


@app.post("/products/{sku_id}/approve")
def approve_product(sku_id: int,
                    body: ReviewAction | None = None,
                    user: User = Depends(require_permission("faq.review"))):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE sku
            SET status='approved', approved_by=%s, approved_at=now(),
                version=version+1
            WHERE id=%s AND status='draft'
            RETURNING sku_code
        """, (user.name, sku_id))
        row = cur.fetchone()
        conn.commit()
    if not row:
        raise HTTPException(409, "仅 draft 产品可审核通过")
    return {"ok": True, "id": sku_id, "sku_code": row[0],
            "status": "approved", "by": user.name}


@app.post("/products/{sku_id}/reject")
def reject_product(sku_id: int,
                   body: ReviewAction | None = None,
                   user: User = Depends(require_permission("faq.review"))):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE sku SET status='deprecated', approved_by=%s,
                           approved_at=now(), version=version+1
            WHERE id=%s AND status='draft'
        """, (user.name, sku_id))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(409, "仅 draft 产品可驳回")
    return {"ok": True, "id": sku_id, "status": "deprecated"}


@app.get("/offers")
def list_offers(status: str | None = None,
                q: str | None = None,
                user: User = Depends(require_permission("faq.view"))):
    where, params = [], []
    if status:
        where.append("o.status=%s"); params.append(status)
    if q:
        where.append("(s.sku_code ILIKE %s OR s.name_en ILIKE %s "
                     "OR s.name_zh ILIKE %s)")
        params.extend([f"%{q}%"] * 3)
    sql = f"""
        SELECT o.id, s.id AS sku_id, s.sku_code, v.variant_code,
               s.name_zh, s.name_en, o.currency, o.unit_price, o.moq,
               o.lead_time_days, o.freight_mode, o.valid_until, o.status,
               o.version, o.approved_by, o.approved_at
        FROM offer o
        JOIN variant v ON v.id=o.variant_id
        JOIN sku s ON s.id=v.sku_id
        {"WHERE " + " AND ".join(where) if where else ""}
        ORDER BY o.id DESC LIMIT 200
    """
    with _conn() as conn, conn.cursor() as cur:
        cur.execute(sql, params)
        rows = cur.fetchall()
    cols = ["id", "sku_id", "sku_code", "variant_code", "name_zh", "name_en",
            "currency", "unit_price", "moq", "lead_time_days", "freight_mode",
            "valid_until", "status", "version", "approved_by", "approved_at"]
    return [dict(zip(cols, row)) for row in rows]


@app.post("/offers/{offer_id}/approve")
def approve_offer(offer_id: int,
                  body: OfferReviewAction | None = None,
                  user: User = Depends(require_permission("faq.review"))):
    """Publish one price atomically and expire the prior active price."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT o.variant_id, o.valid_until, s.status
            FROM offer o
            JOIN variant v ON v.id=o.variant_id
            JOIN sku s ON s.id=v.sku_id
            WHERE o.id=%s AND o.status='draft'
            FOR UPDATE
        """, (offer_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(409, "仅 draft 报价可审核通过")
        variant_id, valid_until, sku_status = row
        if sku_status != "approved":
            raise HTTPException(409, "请先审核通过对应产品")
        if valid_until is None or valid_until.isoformat() < (
            datetime.now(timezone.utc).date().isoformat()
        ):
            raise HTTPException(409, "报价已过有效期，不能发布")
        cur.execute("""
            UPDATE offer SET status='expired', version=version+1
            WHERE variant_id=%s AND status='approved'
        """, (variant_id,))
        cur.execute("""
            UPDATE offer
            SET status='approved', approved_by=%s, approved_at=now(),
                version=version+1
            WHERE id=%s
        """, (user.name, offer_id))
        conn.commit()
    return {"ok": True, "id": offer_id, "status": "approved",
            "by": user.name}


@app.post("/offers/{offer_id}/reject")
def reject_offer(offer_id: int,
                 body: OfferReviewAction | None = None,
                 user: User = Depends(require_permission("faq.review"))):
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE offer SET status='rejected', approved_by=%s,
                             approved_at=now(), version=version+1
            WHERE id=%s AND status='draft'
        """, (user.name, offer_id))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(409, "仅 draft 报价可驳回")
    return {"ok": True, "id": offer_id, "status": "rejected"}


# ============ 审核工作流（审核员以上） ============

@app.post("/faq/{faq_id}/approve")
def approve_faq(faq_id: int, body: ReviewAction | None = None,
                user: User = Depends(require_permission("faq.review"))):
    """Approve a candidate and atomically publish its immutable snapshot."""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, version, question, answer, category, tags, sources,
                   risk_level, action_level, permission, customer_ready,
                   valid_from, valid_until, embedding
            FROM faq WHERE id=%s AND status='draft' FOR UPDATE
        """, (faq_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(409, "仅 draft 状态可审核通过")
        cols = ["id", "version", "question", "answer", "category", "tags",
                "sources", "risk_level", "action_level", "permission",
                "customer_ready", "valid_from", "valid_until", "embedding"]
        item = dict(zip(cols, row))
        embedding = item.pop("embedding")
        if embedding is None:
            try:
                from src.providers.embedding_provider import get_embedding_provider
                embedding = get_embedding_provider().embed(
                    [item["question"] + "\n" + item["answer"]]
                )[0]
            except Exception as exc:
                raise HTTPException(
                    503, f"无法生成知识向量，暂不能发布：{exc}"
                ) from exc
        if isinstance(embedding, (list, tuple)):
            vector_text = "[" + ",".join(f"{float(x):.8f}" for x in embedding) + "]"
        elif isinstance(embedding, str):
            vector_text = embedding
        else:
            vector_text = "[" + ",".join(
                f"{float(x):.8f}" for x in list(embedding)
            ) + "]"
        snapshot = json.dumps({
            k: (v.isoformat() if hasattr(v, "isoformat") else v)
            for k, v in item.items() if k != "id"
        }, ensure_ascii=False)
        cur.execute("""
            UPDATE faq_version
            SET status='superseded'
            WHERE faq_id=%s AND status='approved'
        """, (faq_id,))
        cur.execute("""
            INSERT INTO faq_version
              (faq_id, version, snapshot, status, created_by, approved_by,
               approved_at)
            VALUES (%s,%s,%s::jsonb,'approved',%s,%s,now())
            ON CONFLICT (faq_id, version) DO UPDATE
              SET snapshot=EXCLUDED.snapshot, status='approved',
                  approved_by=EXCLUDED.approved_by, approved_at=now()
        """, (faq_id, item["version"], snapshot, user.name, user.name))
        cur.execute("""
            UPDATE faq
            SET status='approved', approved_by=%s, approved_at=now(),
                embedding=%s::vector, published_snapshot=%s::jsonb,
                published_embedding=%s::vector, published_version=version
            WHERE id=%s
        """, (user.name, vector_text, snapshot, vector_text, faq_id))
        conn.commit()
    return {
        "ok": True, "id": faq_id, "status": "approved",
        "published_version": item["version"], "by": user.name,
    }


@app.post("/faq/{faq_id}/reject")
def reject_faq(faq_id: int, body: ReviewAction | None = None,
               user: User = Depends(require_permission("faq.review"))):
    """审核驳回：draft → rejected（保持可编辑）。"""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE faq SET status='deprecated', approved_by=%s, "
                    "approved_at=now() WHERE id=%s AND status='draft'",
                    (user.name, faq_id))
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(409, "仅 draft 状态可驳回")
    return {"ok": True, "id": faq_id, "status": "deprecated"}


# ============ 入录批次查询（管理员） ============

@app.get("/batches")
def list_batches(user: User = Depends(require_permission("ingest.view"))):
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


# ============ 账户登录认证（httpOnly Cookie 会话） ============

SESSION_COOKIE = "kh_session"

class LoginBody(BaseModel):
    username: str
    password: str


@app.post("/auth/login")
def login(body: LoginBody, request: Request):
    """账号密码登录 → 下发 httpOnly 会话 Cookie（SameSite=Lax 防跨站）。"""
    from src.auth import auth as _auth
    ip = request.client.host if request.client else ""
    info = _auth.authenticate(body.username, body.password, DSN, ip=ip)
    if info is None:
        raise HTTPException(401, "用户名或密码错误")
    if info.get("locked"):
        raise HTTPException(429, f"失败次数过多，已锁定 {LOGIN_LOCK_MINUTES} 分钟")
    if info.get("disabled"):
        raise HTTPException(403, "账户已停用，请联系管理员")
    resp = JSONResponse({"ok": True,
                         "user": {"id": info["id"], "username": info.get("username", ""),
                                  "name": info["name"], "roles": info.get("roles", [])},
                         "must_change": info.get("must_change", False)})
    resp.set_cookie(SESSION_COOKIE, info["token"],
                    httponly=True, samesite="lax", max_age=SESSION_TTL_HOURS * 3600,
                    path="/", secure=os.environ.get("COOKIE_SECURE", "0") == "1")
    return resp


@app.post("/auth/logout")
def logout(request: Request, user: User = Depends(get_current_user)):
    """登出：销毁当前会话 token 并清除 Cookie。"""
    from src.auth import auth as _auth
    token = request.cookies.get(SESSION_COOKIE) or ""
    if not token:
        auth_header = request.headers.get("Authorization", "")
        token = auth_header[7:].strip() if auth_header.startswith("Bearer ") else ""
    if token:
        _auth.delete_session(token, DSN)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(SESSION_COOKIE, path="/")
    return resp


@app.get("/auth/me")
def me(user: User = Depends(get_current_user)):
    """当前登录账户信息（前端鉴权初始化用）。"""
    return {"id": user.id, "username": user.username, "name": user.name,
            "roles": user.roles, "permissions": sorted(user.permissions),
            "data_scope": user.data_scope}


class ChangePasswordBody(BaseModel):
    old_password: str
    new_password: str


@app.post("/auth/password")
def change_password(body: ChangePasswordBody, request: Request,
                    user: User = Depends(get_current_user)):
    """修改自己的密码；改密后注销除当前会话外的其他会话。"""
    from src.auth import auth as _auth
    if len(body.new_password) < 8:
        raise HTTPException(400, "新密码至少 8 位")
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT password_hash FROM system_account WHERE id=%s",
                    (user.id,))
        row = cur.fetchone()
    if not row or not _auth.verify_password(body.old_password, row[0]):
        raise HTTPException(400, "当前密码错误")
    token = request.cookies.get(SESSION_COOKIE) or ""
    _auth.set_password(user.id, body.new_password, DSN)
    _auth.revoke_sessions(user.id, keep_token=token or None, dsn=DSN)
    return {"ok": True, "msg": "密码已修改，其他设备已下线"}


@app.get("/health")
def health():
    """健康检查：进程存活 + 数据库连通 + 模型就绪（供监控探活）。"""
    db_ok = True
    try:
        with psycopg.connect(DSN, connect_timeout=3) as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception as e:
        db_ok = str(e)
    return {
        "status": "ok" if db_ok is True else "degraded",
        "service": "admin-api",
        "database": "ok" if db_ok is True else "error",
        "embedding": "ready" if _embedding_provider is not None else "cold",
        "ts": datetime.now(timezone.utc).isoformat(),
    }


# ============ 评测与召回审计（reviewer 以上） ============

GOLDSET_PATH = os.environ.get("GOLDSET_PATH",
                              "data/goldset/jiacong_faq_goldset_v2.json")


@app.post("/eval/run")
def run_eval(dev: bool = False,
             user: User = Depends(require_permission("eval.run"))):
    """触发一次评测；生产门禁默认只使用已发布快照。"""
    from src.core import eval_runner
    cases = eval_runner.load_goldset(GOLDSET_PATH)
    result = eval_runner.run(cases, DSN, include_draft=dev)
    rid = eval_runner.save_report(
        result, GOLDSET_PATH, DSN,
        mode="dev" if dev else "production",
    )
    return {"report_id": rid, "gate_passed": result["gate_passed"],
            "metrics": {k: v for k, v in result["metrics"].items()
                        if k != "retrieval"},
            "retrieval": {k: v for k, v in result["metrics"]["retrieval"].items()
                          if k != "per_case"}}


@app.get("/eval/reports")
def list_eval_reports(limit: int = Query(default=20, le=100),
                      user: User = Depends(require_permission("eval.view"))):
    """评测历史（趋势看板数据）。"""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, goldset_name, backend, mode, recall_at_5, precision_at_5,
                   mrr_at_5, fact_accuracy, red_line_errors, gate_passed,
                   total_cases, hit_cases, created_at
            FROM eval_report ORDER BY id DESC LIMIT %s
        """, (limit,))
        rows = cur.fetchall()
    cols = ["id", "goldset_name", "backend", "mode", "recall_at_5", "precision_at_5",
            "mrr_at_5", "fact_accuracy", "red_line_errors", "gate_passed",
            "total_cases", "hit_cases", "created_at"]
    return [dict(zip(cols, r)) for r in rows]


@app.get("/eval/reports/{report_id}")
def get_eval_report(report_id: int,
                    user: User = Depends(require_permission("eval.view"))):
    """报告明细：含 per-case 召回审计（query/hit/top5/rr）与红线详情。"""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, goldset_name, backend, mode, recall_at_5, precision_at_5,
                   mrr_at_5, fact_accuracy, red_line_errors, gate_passed,
                   total_cases, hit_cases, detail_json, created_at
            FROM eval_report WHERE id=%s
        """, (report_id,))
        row = cur.fetchone()
    if not row:
        raise HTTPException(404, "评测报告不存在")
    cols = ["id", "goldset_name", "backend", "mode", "recall_at_5", "precision_at_5",
            "mrr_at_5", "fact_accuracy", "red_line_errors", "gate_passed",
            "total_cases", "hit_cases", "detail_json", "created_at"]
    d = dict(zip(cols, row))
    d["detail"] = d.pop("detail_json")
    return d


@app.get("/audit-log")
def list_audit_log(limit: int = Query(default=50, le=200),
                   decision: str | None = None,
                   user: User = Depends(require_permission("audit.view"))):
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


# ============ 数据入录（editor 以上可上传/执行，reviewer 以上看明细） ============

import hashlib
import pathlib
import shutil
import tempfile
from fastapi import UploadFile, File, Form

UPLOAD_DIR = pathlib.Path("data/uploads")
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)


@app.post("/upload")
def upload_file(file: UploadFile = File(...),
                user: User = Depends(require_permission("ingest.upload"))):
    """上传原始数据文件（xlsx/json/docx…），存 data/uploads，返回路径与哈希。"""
    suffix = pathlib.Path(file.filename or "file").suffix.lower()
    if suffix not in {".xlsx", ".xls", ".json", ".docx", ".csv"}:
        raise HTTPException(400, f"不支持的文件类型 {suffix}（支持 xlsx/json/docx/csv）")
    content = file.file.read()
    if len(content) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"文件超过上限 {MAX_UPLOAD_MB}MB")
    sha = hashlib.sha256(content).hexdigest()[:16]
    dest = UPLOAD_DIR / f"{sha}{suffix}"
    dest.write_bytes(content)
    return {"filename": file.filename, "path": str(dest), "size": len(content),
            "sha256": sha, "kind": "faq" if suffix == ".json" else "facts"}


class IngestRunBody(BaseModel):
    path: str
    kind: str = "facts"          # facts | faq
    config: str | None = None    # 映射配置名，默认 jiacong_facts


class SourceAnalyzeBody(BaseModel):
    path: str
    target: str = "product"


class ImportPreviewBody(BaseModel):
    path: str
    template_key: str
    target: str | None = None
    overrides: dict[str, str] = {}
    selected_sheets: list[str] = Field(default_factory=list)
    limit: int = Field(default=50, ge=1, le=500)


class ImportExecuteBody(BaseModel):
    path: str
    template_key: str
    target: str | None = None
    overrides: dict[str, str] = {}
    selected_sheets: list[str] = Field(default_factory=list)


class ImportTemplateBody(BaseModel):
    config_key: str
    description: str = ""
    config: dict
    active: bool = True


def _mapping_config(config_key: str) -> dict:
    from src.config_loader import get_mapping
    try:
        return get_mapping(config_key, DSN)
    except (FileNotFoundError, KeyError):
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT config FROM mapping_config
                WHERE config_key=%s AND active
            """, (config_key,))
            row = cur.fetchone()
        if not row:
            raise HTTPException(404, f"导入模板不存在: {config_key}")
        config = row[0] if isinstance(row[0], dict) else json.loads(row[0])
        config["_source"] = "db"
        return config


@app.post("/ingest/run")
def ingest_run(body: IngestRunBody,
               user: User = Depends(require_permission("ingest.run"))):
    """执行入录：文件 → 解析/映射/校验/入库，返回批次统计。"""
    from src.ingest import pipeline
    try:
        result = pipeline.run(body.path, body.kind, body.config)
    except FileNotFoundError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, f"入录失败: {e}")
    return {"ok": True, "by": user.name, **result}


@app.get("/api/v1/import/templates")
def import_templates(user: User = Depends(require_permission("ingest.view"))):
    from src.config_loader import CONFIG_DIR
    templates: dict[str, dict] = {}
    if CONFIG_DIR.exists():
        for path in CONFIG_DIR.glob("*.yaml"):
            try:
                cfg = _mapping_config(path.stem)
                templates[path.stem] = {
                    "key": path.stem,
                    "source": cfg.get("_source"),
                    "description": cfg.get("description", ""),
                    "config": cfg,
                }
            except Exception:
                continue
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT config_key, description, config, version, active
            FROM mapping_config WHERE active ORDER BY config_key
        """)
        for key, description, cfg, version, active in cur.fetchall():
            value = cfg if isinstance(cfg, dict) else json.loads(cfg)
            templates[key] = {
                "key": key, "source": "db", "description": description,
                "version": version, "active": active, "config": value,
            }
    return {"templates": list(templates.values())}


@app.post("/api/v1/import/templates")
def save_import_template(body: ImportTemplateBody,
                         user: User = Depends(require_permission("ingest.view"))):
    if not body.config_key.strip():
        raise HTTPException(400, "config_key 不能为空")
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO mapping_config
              (config_key, config, description, version, active, updated_by)
            VALUES (%s,%s::jsonb,%s,1,%s,%s)
            ON CONFLICT (config_key) DO UPDATE
              SET config=EXCLUDED.config,
                  description=EXCLUDED.description,
                  version=mapping_config.version + 1,
                  active=EXCLUDED.active,
                  updated_by=EXCLUDED.updated_by,
                  updated_at=now()
            RETURNING config_key, version
        """, (body.config_key.strip(),
              json.dumps(body.config, ensure_ascii=False),
              body.description, body.active, user.name))
        key, version = cur.fetchone()
        cur.execute("""
            INSERT INTO mapping_config_version
              (config_key, version, config, description, created_by)
            VALUES (%s,%s,%s::jsonb,%s,%s)
            ON CONFLICT (config_key, version) DO NOTHING
        """, (key, version, json.dumps(body.config, ensure_ascii=False),
              body.description, user.name))
        conn.commit()
    return {"ok": True, "config_key": key, "version": version}


@app.post("/api/v1/import/analyze")
def analyze_import_source(body: SourceAnalyzeBody,
                          user: User = Depends(require_permission("ingest.run"))):
    from src.ingest.studio import analyze_source, suggest_mapping
    try:
        result = analyze_source(body.path)
        for sheet in result["sheets"]:
            sheet["suggested_mapping"] = suggest_mapping(
                sheet.get("headers", []), body.target
            )
        return result
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/v1/import/preview")
def preview_import_data(body: ImportPreviewBody,
                        user: User = Depends(require_permission("ingest.run"))):
    from src.ingest.studio import preview_import
    cfg = _mapping_config(body.template_key)
    try:
        return preview_import(body.path, cfg, body.target,
                              body.overrides, body.limit,
                              body.selected_sheets)
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/v1/import/execute")
def execute_import_data(body: ImportExecuteBody,
                        user: User = Depends(require_permission("ingest.run"))):
    from src.ingest.studio import execute_import
    cfg = _mapping_config(body.template_key)
    try:
        result = execute_import(
            body.path, cfg, DSN, body.target, body.overrides, user.name,
            body.selected_sheets,
        )
    except (FileNotFoundError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    if not result.get("ok"):
        raise HTTPException(422, {
            "message": "预检未通过，未写入数据库",
            "preview": result,
        })
    return result


@app.get("/batches/{batch_id}")
def get_batch(batch_id: int,
              user: User = Depends(require_permission("ingest.view"))):
    """批次详情：统计 + raw_data 明细（bad/pending_manual 可审计）。"""
    with _conn() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT id, source_file, source_kind, total_rows, loaded_rows,
                   pending_rows, bad_rows, status, error_msg, started_by,
                   started_at, finished_at
            FROM import_batch WHERE id=%s
        """, (batch_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "批次不存在")
        cur.execute("""
            SELECT id, sheet_name, row_no, verdict, errs, mapped
            FROM raw_data WHERE batch_id=%s AND verdict IN ('bad','pending_manual')
            ORDER BY id LIMIT 200
        """, (batch_id,))
        bads = cur.fetchall()
    cols = ["id", "source_file", "source_kind", "total_rows", "loaded_rows",
            "pending_rows", "bad_rows", "status", "error_msg", "started_by",
            "started_at", "finished_at"]
    d = dict(zip(cols, row))
    d["problem_rows"] = [
        {"id": r[0], "sheet": r[1], "row_no": r[2], "verdict": r[3],
         "errs": r[4], "mapped": r[5]} for r in bads]
    return d


@app.get("/mapping-config")
def list_mapping_configs(user: User = Depends(require_permission("ingest.view"))):
    """可用映射配置（YAML 基线 + DB 运行时通道）。"""
    from src.config_loader import CONFIG_DIR
    cfgs = [p.stem for p in CONFIG_DIR.glob("*.yaml")] if CONFIG_DIR.exists() else []
    # 附加 DB 通道配置
    try:
        with _conn() as conn, conn.cursor() as cur:
            cur.execute("SELECT config_key, active FROM mapping_config ORDER BY config_key")
            db_cfgs = [{"key": r[0], "active": r[1]} for r in cur.fetchall()]
    except Exception:
        db_cfgs = []
    return {"yaml": cfgs, "db": db_cfgs}


# ============ 权限管理（system_ 账户/角色/权限树，仅 admin） ============

class AccountCreate(BaseModel):
    username: str
    name: str | None = None
    roles: list[str] = []
    initial_password: str | None = None


class AccountRolesUpdate(BaseModel):
    roles: list[str] = []


class AccountPasswordReset(BaseModel):
    new_password: str | None = None


class RoleCreate(BaseModel):
    code: str
    name: str
    description: str | None = None
    data_scope: str = "full"          # 'standard'=public+internal；'full'=全部
    permissions: list[str] = []


class RolePermissionsUpdate(BaseModel):
    permissions: list[str] = []
    data_scope: str | None = None   # 可选：一并更新数据可见范围


@app.get("/permissions")
def list_permissions_api(user: User = Depends(require_permission("role.manage"))):
    """权限点（模块→页面→功能树），供角色分配勾选。"""
    from src.auth import rbac
    return {"permissions": rbac.list_permissions(DSN)}


@app.get("/roles")
def list_roles_api(user: User = Depends(require_permission("role.manage"))):
    from src.auth import rbac
    return {"roles": rbac.list_roles(DSN)}


@app.post("/roles")
def create_role_api(body: RoleCreate,
                    user: User = Depends(require_permission("role.manage"))):
    from src.auth import rbac
    try:
        r = rbac.create_role(body.code, body.name, body.description or "",
                             body.data_scope, body.permissions, DSN)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "role": r}


@app.put("/roles/{role_id}/permissions")
def update_role_permissions_api(
        role_id: int, body: RolePermissionsUpdate,
        user: User = Depends(require_permission("role.manage"))):
    """替换式重设角色权限点（可选同时更新数据范围）。"""
    from src.auth import rbac
    try:
        rbac.update_role_permissions(role_id, body.permissions, DSN)
        if body.data_scope in ("full", "standard"):
            with psycopg.connect(DSN) as conn, conn.cursor() as cur:
                cur.execute("UPDATE system_role SET data_scope=%s WHERE id=%s",
                            (body.data_scope, role_id))
                conn.commit()
        r = next(rr for rr in rbac.list_roles(DSN) if rr["id"] == role_id)
    except (ValueError, StopIteration) as e:
        raise HTTPException(404, "角色不存在")
    return {"ok": True, "role": r}


class RoleMetaUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    data_scope: str | None = None


@app.put("/roles/{role_id}")
def update_role_meta_api(role_id: int, body: RoleMetaUpdate,
                         user: User = Depends(require_permission("role.manage"))):
    """角色元信息更新（重命名/说明/数据范围；code 不可变）。"""
    from src.auth import rbac
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT id FROM system_role WHERE id=%s", (role_id,))
        if not cur.fetchone():
            raise HTTPException(404, "角色不存在")
        fields, params = [], []
        if body.name is not None:
            fields.append("name=%s"); params.append(body.name)
        if body.description is not None:
            fields.append("description=%s"); params.append(body.description)
        if body.data_scope in ("full", "standard"):
            fields.append("data_scope=%s"); params.append(body.data_scope)
        if fields:
            cur.execute(f"UPDATE system_role SET {', '.join(fields)} WHERE id=%s",
                        (*params, role_id))
            conn.commit()
    r = next(rr for rr in rbac.list_roles(DSN) if rr["id"] == role_id)
    return {"ok": True, "role": r}


@app.delete("/roles/{role_id}")
def delete_role_api(role_id: int,
                    user: User = Depends(require_permission("role.manage"))):
    from src.auth import rbac
    try:
        rbac.delete_role(role_id, DSN)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.get("/accounts")
def list_accounts_api(user: User = Depends(require_permission("account.manage"))):
    """账户列表（含绑定角色），供管理端账户管理展示。"""
    from src.auth import rbac
    return {"accounts": rbac.list_accounts(DSN)}


@app.post("/accounts")
def create_account_api(body: AccountCreate,
                       user: User = Depends(require_permission("account.manage"))):
    """新建账户并绑定角色；返回临时密码（未指定时随机）。"""
    from src.auth import rbac
    try:
        acct, temp_password = rbac.create_account(
            body.username, body.name or body.username, body.roles or [],
            body.initial_password, DSN)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True, "account": acct,
            "temporary_password": temp_password}


@app.put("/accounts/{account_id}/roles")
def change_account_roles(account_id: int, body: AccountRolesUpdate,
                         user: User = Depends(require_permission("account.manage"))):
    """替换式重绑账户角色。"""
    from src.auth import rbac
    rbac.update_account_roles(account_id, body.roles, DSN)
    acct = next(a for a in rbac.list_accounts(DSN) if a["id"] == account_id)
    return {"ok": True, "account": acct}


@app.put("/accounts/{account_id}/enabled")
def set_account_enabled_api(account_id: int, body: dict,
                            user: User = Depends(require_permission("account.manage"))):
    """启用/停用账户；停用同时注销其会话。内置管理员不可停用。"""
    from src.auth import rbac
    enabled = bool(body.get("enabled", True))
    try:
        rbac.set_account_enabled(account_id, enabled, DSN)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/accounts/{account_id}/reset-password")
def reset_account_password_api(
        account_id: int, body: AccountPasswordReset,
        user: User = Depends(require_permission("account.manage"))):
    """管理员重置密码（指定或随机）；重置后强制首登改密、注销会话。"""
    from src.auth import rbac
    pw = rbac.reset_account_password(account_id, body.new_password, DSN)
    return {"ok": True, "temporary_password": pw}
