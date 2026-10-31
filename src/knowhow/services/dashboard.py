"""Actionable owner dashboard and onboarding progress."""
from __future__ import annotations

from knowhow.db import connect


def dashboard(organization_id: int) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT
              (SELECT count(*) FROM knowledge_unit
               WHERE organization_id=%s AND status='published') AS published,
              (SELECT count(*) FROM knowledge_unit
               WHERE organization_id=%s AND status='draft') AS drafts,
              (SELECT count(*) FROM account
               WHERE organization_id=%s AND enabled) AS teammates,
              (SELECT count(*) FROM interaction
               WHERE organization_id=%s
                 AND created_at >= now()-interval '7 days') AS weekly_interactions,
              (SELECT count(*) FROM knowledge_gap
               WHERE organization_id=%s AND status='open') AS open_gaps,
              (SELECT count(*) FROM interaction
               WHERE organization_id=%s AND status='sent'
                 AND created_at >= now()-interval '30 days') AS sent_30d,
              (SELECT count(*) FROM interaction
               WHERE organization_id=%s AND status='sent'
                 AND edit_distance IS NOT NULL AND edit_distance <= 0.2
                 AND created_at >= now()-interval '30 days') AS low_edit_sent,
              (SELECT count(*) FROM knowledge_unit
               WHERE organization_id=%s AND status='published'
                 AND valid_until IS NOT NULL
                 AND valid_until <= CURRENT_DATE+14) AS expiring
            """,
            (organization_id,) * 8,
        )
        metrics = cur.fetchone()
        cur.execute(
            """
            SELECT id, question, reason, priority, created_at
            FROM knowledge_gap
            WHERE organization_id=%s AND status='open'
            ORDER BY priority DESC, created_at DESC
            LIMIT 8
            """,
            (organization_id,),
        )
        gaps = cur.fetchall()
        cur.execute(
            """
            SELECT k.id, k.title, k.kind, k.status, k.updated_at,
                   a.display_name AS owner_name
            FROM knowledge_unit k
            LEFT JOIN account a ON a.id=k.owner_id
            WHERE k.organization_id=%s
            ORDER BY k.updated_at DESC LIMIT 6
            """,
            (organization_id,),
        )
        recent_knowledge = cur.fetchall()
    checklist = [
        {"key": "company", "label": "完成公司与负责人初始化", "done": True},
        {
            "key": "knowledge",
            "label": "发布至少 20 条知识",
            "done": metrics["published"] >= 20,
        },
        {
            "key": "team",
            "label": "添加至少 2 名团队成员",
            "done": metrics["teammates"] >= 2,
        },
        {
            "key": "usage",
            "label": "完成至少 10 次销售问答",
            "done": metrics["weekly_interactions"] >= 10,
        },
        {
            "key": "feedback",
            "label": "标记至少 1 次客户回复已发送",
            "done": metrics["sent_30d"] >= 1,
        },
    ]
    readiness = round(
        sum(1 for item in checklist if item["done"]) * 100 / len(checklist),
    )
    acceptance = (
        round(metrics["low_edit_sent"] * 100 / metrics["sent_30d"], 1)
        if metrics["sent_30d"] else 0
    )
    actions = []
    if metrics["open_gaps"]:
        actions.append({
            "title": f"处理 {metrics['open_gaps']} 个知识缺口",
            "detail": "优先处理高优先级问题，补成可发布知识。",
            "target": "review",
        })
    if metrics["drafts"]:
        actions.append({
            "title": f"审核 {metrics['drafts']} 条知识草稿",
            "detail": "确认来源和适用范围后再发布。",
            "target": "knowledge",
        })
    if metrics["expiring"]:
        actions.append({
            "title": f"{metrics['expiring']} 条知识将在 14 天内过期",
            "detail": "更新有效期或重新确认内容。",
            "target": "knowledge",
        })
    if not actions:
        actions.append({
            "title": "今天没有紧急知识风险",
            "detail": "让业务员继续使用工作台，系统会从真实问题生成新的待办。",
            "target": "ask",
        })
    return {
        "metrics": {
            **metrics,
            "draft_acceptance": acceptance,
            "readiness": readiness,
        },
        "checklist": checklist,
        "actions": actions,
        "recent_gaps": gaps,
        "recent_knowledge": recent_knowledge,
    }

