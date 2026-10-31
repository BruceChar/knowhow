"""Customer-service feedback ingestion and knowledge-loop resolution."""
from __future__ import annotations

import json
from typing import Any

from src.auth.rbac import User

FEEDBACK_VERDICTS = {"correct", "incorrect", "needs_review"}


def first_faq_id(citations: Any) -> int | None:
    """Return the first real FAQ id from the audit citation payload.

    Older rows may contain a JSON string rather than a decoded list. Ignore
    malformed values so feedback still works when a cited card was removed.
    """
    value = citations
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return None
    if not isinstance(value, list):
        return None
    for item in value:
        candidate = item.get("id") if isinstance(item, dict) else item
        try:
            faq_id = int(candidate)
        except (TypeError, ValueError):
            continue
        if faq_id > 0:
            return faq_id
    return None


def result_view(
    verdict: str,
    issue_id: int | None,
    issue_status: str | None,
    faq_id: int | None = None,
    faq_version: int | None = None,
    resolution: str | None = None,
) -> dict:
    """Build a stable, user-facing feedback result."""
    if verdict == "correct":
        label = "已记录：回答正确"
    elif issue_status == "resolved" and faq_id:
        version = f" v{faq_version}" if faq_version else ""
        label = f"已采纳：知识卡 #{faq_id}{version} 已进入待审核队列"
    elif issue_status == "rejected":
        label = "已处理：反馈未采纳"
    else:
        label = "待处理：反馈已进入知识回流队列"
    return {
        "label": label,
        "issue_id": issue_id,
        "issue_status": issue_status,
        "faq_id": faq_id,
        "faq_version": faq_version,
        "resolution": resolution,
    }


def can_view_all_feedback(user: User) -> bool:
    """Reviewers can inspect the full queue; operators only see their own."""
    return user.is_admin or user.has("audit.view") or user.has("suggestion.review")


def _parse_feedback(value: Any) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {"reason": value}
    return parsed if isinstance(parsed, dict) else {}


def submit_feedback(
    conn,
    user: User,
    *,
    inquiry_id: str,
    verdict: str,
    reason: str,
    corrected_answer: str,
) -> dict:
    """Persist one judgement and return its current processing result.

    Repeating feedback for the same inquiry updates the same open issue instead
    of creating duplicate work. A later "correct" judgement closes an open
    issue as rejected, preserving the original audit trail.
    """
    feedback_text = json.dumps(
        {
            "verdict": verdict,
            "reason": reason,
            "corrected_answer": corrected_answer,
            "by": user.name,
            "by_account_id": user.id,
        },
        ensure_ascii=False,
    )
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, citations
            FROM audit_log
            WHERE inquiry_id=%s
            ORDER BY id DESC
            LIMIT 1
            FOR UPDATE
            """,
            (inquiry_id,),
        )
        audit = cur.fetchone()
        if not audit:
            raise LookupError("问答记录不存在")
        audit_id, citations = audit
        cur.execute(
            """
            UPDATE audit_log
            SET user_feedback=%s, feedback_verdict=%s, feedback_at=now(),
                feedback_by=%s, feedback_by_account_id=%s
            WHERE id=%s
            """,
            (feedback_text, verdict, user.name, user.id, audit_id),
        )
        cur.execute(
            """
            SELECT id, status FROM issue
            WHERE related_audit=%s AND status='open'
            ORDER BY id DESC LIMIT 1 FOR UPDATE
            """,
            (audit_id,),
        )
        existing = cur.fetchone()

        if verdict == "correct":
            issue_id = existing[0] if existing else None
            if existing:
                cur.execute(
                    """
                    UPDATE issue
                    SET status='rejected',
                        resolution=COALESCE(NULLIF(resolution, ''),
                                            '客服复核后确认原回答正确'),
                        resolved_by=%s, resolved_at=now(), updated_at=now()
                    WHERE id=%s
                    """,
                    (user.name, issue_id),
                )
            result = result_view("correct", issue_id, "rejected" if issue_id else None)
        else:
            faq_id = first_faq_id(citations)
            if existing:
                issue_id = existing[0]
                cur.execute(
                    """
                    UPDATE issue
                    SET feedback=%s, feedback_verdict=%s, suggested_answer=%s,
                        faq_id=COALESCE(%s, faq_id), updated_at=now()
                    WHERE id=%s
                    """,
                    (feedback_text, verdict, corrected_answer, faq_id, issue_id),
                )
            else:
                cur.execute(
                    """
                    INSERT INTO issue
                      (faq_id, inquiry_id, feedback, feedback_verdict,
                       suggested_answer, status, created_by,
                       created_by_account_id, related_audit)
                    VALUES (%s,%s,%s,%s,%s,'open',%s,%s,%s)
                    RETURNING id
                    """,
                    (
                        faq_id, inquiry_id, feedback_text, verdict,
                        corrected_answer, user.name, user.id, audit_id,
                    ),
                )
                issue_id = cur.fetchone()[0]
            cur.execute(
                "UPDATE audit_log SET issue_id=%s WHERE id=%s",
                (issue_id, audit_id),
            )
            result = result_view(verdict, issue_id, "open", faq_id=faq_id)
        conn.commit()
    return {
        "ok": True,
        "inquiry_id": inquiry_id,
        "verdict": verdict,
        "reason": reason,
        "corrected_answer": corrected_answer,
        **result,
    }


def list_feedback(
    conn,
    user: User,
    *,
    status: str | None = None,
    verdict: str | None = None,
    limit: int = 100,
) -> list[dict]:
    """List feedback receipts with issue processing state."""
    where = ["a.user_feedback IS NOT NULL"]
    params: list[Any] = []
    if status:
        where.append("i.status=%s")
        params.append(status)
    if verdict:
        where.append("a.feedback_verdict=%s")
        params.append(verdict)
    if not can_view_all_feedback(user):
        where.append("a.feedback_by_account_id=%s")
        params.append(user.id)
    params.append(limit)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT a.inquiry_id, a.question, a.answer, a.citations,
                   a.user_feedback, a.feedback_verdict, a.feedback_at,
                   a.feedback_by, i.id, i.status, i.feedback_verdict,
                   i.suggested_answer, i.resolution, i.faq_id, i.faq_version,
                   i.resolved_by, i.resolved_at
            FROM audit_log a
            LEFT JOIN LATERAL (
              SELECT x.* FROM issue x
              WHERE x.id=a.issue_id OR x.related_audit=a.id
              ORDER BY x.id DESC LIMIT 1
            ) i ON true
            WHERE {' AND '.join(where)}
            ORDER BY a.feedback_at DESC NULLS LAST, a.id DESC
            LIMIT %s
            """,
            params,
        )
        rows = cur.fetchall()
    result = []
    for row in rows:
        parsed = _parse_feedback(row[4])
        row_verdict = row[5] or parsed.get("verdict") or "needs_review"
        issue_id, issue_status = row[8], row[9]
        result.append(
            {
                "inquiry_id": row[0],
                "question": row[1],
                "answer": row[2],
                "citations": row[3],
                "verdict": row_verdict,
                "reason": parsed.get("reason", ""),
                "feedback_at": row[6],
                "feedback_by": row[7],
                "issue": (
                    {
                        "id": issue_id,
                        "status": issue_status,
                        "resolution": row[12],
                        "faq_id": row[13],
                        "faq_version": row[14],
                        "resolved_by": row[15],
                        "resolved_at": row[16],
                    }
                    if issue_id
                    else None
                ),
                "suggested_answer": row[11] or parsed.get("corrected_answer", ""),
                "result": result_view(
                    row_verdict, issue_id, issue_status,
                    row[13], row[14], row[12],
                ),
            }
        )
    return result


def feedback_summary(conn, user: User) -> dict:
    """Return scoped feedback and knowledge-loop quality metrics."""
    params: list[Any] = []
    scope = ""
    if not can_view_all_feedback(user):
        scope = " AND a.feedback_by_account_id=%s"
        params.append(user.id)
    with conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT count(*) FILTER (WHERE a.user_feedback IS NOT NULL),
                   count(*) FILTER (WHERE a.feedback_verdict='correct'),
                   count(*) FILTER (WHERE a.feedback_verdict='incorrect'),
                   count(*) FILTER (WHERE a.feedback_verdict='needs_review'),
                   count(*)
            FROM audit_log a
            WHERE true{scope}
            """,
            params,
        )
        total, correct, incorrect, needs_review, audit_total = cur.fetchone()
        cur.execute(
            f"""
            SELECT count(*) FILTER (WHERE i.status='open'),
                   count(*) FILTER (WHERE i.status='resolved'),
                   count(*) FILTER (WHERE i.status='rejected'),
                   count(*) FILTER (WHERE i.faq_version IS NOT NULL)
            FROM issue i
            JOIN audit_log a ON a.id=i.related_audit
            WHERE true{scope}
            """,
            params,
        )
        open_count, resolved, rejected, applied = cur.fetchone()
    return {
        "feedback_total": total,
        "audit_total": audit_total,
        "feedback_rate": round(total * 100 / audit_total, 1) if audit_total else 0,
        "correct": correct,
        "incorrect": incorrect,
        "needs_review": needs_review,
        "open_issues": open_count,
        "resolved_issues": resolved,
        "rejected_issues": rejected,
        "applied_issues": applied,
    }


def resolve_feedback_issue(
    conn,
    user: User,
    *,
    issue_id: int,
    action: str,
    resolution: str,
    suggested_answer: str = "",
) -> dict:
    """Accept or reject an issue; accepted corrections become draft FAQ versions."""
    if action not in {"accept", "reject"}:
        raise ValueError("action 必须是 accept/reject")
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT i.status, i.suggested_answer, i.faq_id,
                   a.question, i.inquiry_id
            FROM issue i
            LEFT JOIN audit_log a ON a.id=i.related_audit
            WHERE i.id=%s
            FOR UPDATE OF i
            """,
            (issue_id,),
        )
        row = cur.fetchone()
        if not row:
            raise LookupError("反馈问题不存在")
        status, stored_answer, faq_id, question, inquiry_id = row
        if status != "open":
            raise RuntimeError("该反馈已经处理，不能重复处理")

        if action == "reject":
            final_resolution = resolution or "审核后决定不修改现有知识"
            cur.execute(
                """
                UPDATE issue
                SET status='rejected', resolution=%s, resolved_by=%s,
                    resolved_at=now(), updated_at=now()
                WHERE id=%s
                """,
                (final_resolution, user.name, issue_id),
            )
            result = result_view(
                "incorrect", issue_id, "rejected",
                faq_id=faq_id, resolution=final_resolution,
            )
        else:
            answer = (suggested_answer or stored_answer or "").strip()
            if not answer:
                raise ValueError("采纳反馈前必须填写建议答案")
            if faq_id:
                cur.execute("SELECT status FROM faq WHERE id=%s", (faq_id,))
                faq = cur.fetchone()
                if not faq:
                    faq_id = None
                else:
                    cur.execute(
                        """
                        INSERT INTO faq_version
                          (faq_id, version, snapshot, status, created_by,
                           approved_by, approved_at)
                        SELECT id, version,
                               to_jsonb(f) - 'embedding' - 'published_embedding'
                                 - 'published_snapshot',
                               'superseded', approved_by, approved_by, approved_at
                        FROM faq f
                        WHERE id=%s AND status='approved'
                        ON CONFLICT (faq_id, version) DO NOTHING
                        """,
                        (faq_id,),
                    )
                    cur.execute(
                        """
                        UPDATE faq
                        SET answer=%s, version=version+1, embedding=NULL,
                            status='draft', updated_at=now()
                        WHERE id=%s
                        RETURNING version
                        """,
                        (answer, faq_id),
                    )
                    faq_version = cur.fetchone()[0]
            if not faq_id:
                cur.execute(
                    """
                    INSERT INTO faq
                      (question, answer, category, status, permission,
                       risk_level, action_level, customer_ready)
                    VALUES (%s,%s,'客服反馈','draft','internal',
                            'medium','B',false)
                    RETURNING id, version
                    """,
                    (question or f"客服反馈：{inquiry_id}", answer),
                )
                faq_id, faq_version = cur.fetchone()
            final_resolution = resolution or "已采纳反馈并生成待审核知识版本"
            cur.execute(
                """
                UPDATE issue
                SET status='resolved', resolution=%s, faq_id=%s,
                    faq_version=%s, suggested_answer=COALESCE(NULLIF(%s, ''),
                                                             suggested_answer),
                    resolved_by=%s, resolved_at=now(), updated_at=now()
                WHERE id=%s
                """,
                (final_resolution, faq_id, faq_version, answer,
                 user.name, issue_id),
            )
            result = result_view(
                "incorrect", issue_id, "resolved",
                faq_id=faq_id, faq_version=faq_version,
                resolution=final_resolution,
            )
        conn.commit()
    return {
        "ok": True,
        "issue_id": issue_id,
        "action": action,
        **result,
    }
