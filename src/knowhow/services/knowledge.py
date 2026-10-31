"""Knowledge authoring, publishing and gap resolution."""
from __future__ import annotations

import json
from typing import Any

from knowhow.db import connect


def list_knowledge(
    organization_id: int,
    *,
    status: str | None = None,
    kind: str | None = None,
    query: str = "",
) -> list[dict]:
    where = ["k.organization_id=%s"]
    params: list[Any] = [organization_id]
    if status:
        where.append("k.status=%s")
        params.append(status)
    if kind:
        where.append("k.kind=%s")
        params.append(kind)
    if query:
        where.append(
            "(k.title ILIKE %s OR k.question ILIKE %s OR k.answer ILIKE %s)"
        )
        params.extend([f"%{query}%"] * 3)
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT k.id, k.kind, k.title, k.question, k.answer, k.status,
                   k.risk_level, k.owner_id, a.display_name AS owner_name,
                   k.evidence_id, k.source_ref, k.valid_until, k.version,
                   k.published_at, k.created_at, k.updated_at
            FROM knowledge_unit k
            LEFT JOIN account a ON a.id=k.owner_id
            WHERE {' AND '.join(where)}
            ORDER BY
              CASE k.status WHEN 'draft' THEN 0 WHEN 'published' THEN 1 ELSE 2 END,
              k.updated_at DESC
            LIMIT 300
            """,
            params,
        )
        return cur.fetchall()


def create_knowledge(
    organization_id: int,
    account_id: int,
    *,
    kind: str,
    title: str,
    question: str,
    answer: str,
    risk_level: str,
    source_ref: str = "",
    valid_until: str | None = None,
    metadata: dict | None = None,
) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO knowledge_unit
              (organization_id,kind,title,question,answer,status,risk_level,
               owner_id,source_ref,valid_until,created_by,metadata)
            VALUES (%s,%s,%s,%s,%s,'draft',%s,%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (
                organization_id, kind, title.strip(), question.strip(),
                answer.strip(), risk_level, account_id, source_ref.strip(),
                valid_until or None, account_id, json.dumps(metadata or {}),
            ),
        )
        knowledge_id = cur.fetchone()["id"]
        conn.commit()
    return {"id": knowledge_id, "status": "draft"}


def update_knowledge(
    organization_id: int,
    knowledge_id: int,
    *,
    title: str,
    question: str,
    answer: str,
    risk_level: str,
    source_ref: str,
    valid_until: str | None,
) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE knowledge_unit
            SET title=%s, question=%s, answer=%s, risk_level=%s,
                source_ref=%s, valid_until=%s, version=version+1,
                status='draft', updated_at=now()
            WHERE id=%s AND organization_id=%s
            RETURNING id, version
            """,
            (
                title.strip(), question.strip(), answer.strip(), risk_level,
                source_ref.strip(), valid_until or None,
                knowledge_id, organization_id,
            ),
        )
        row = cur.fetchone()
        if not row:
            raise LookupError("知识不存在")
        conn.commit()
    return row


def publish_knowledge(organization_id: int, account_id: int, knowledge_id: int) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM knowledge_unit
            WHERE id=%s AND organization_id=%s FOR UPDATE
            """,
            (knowledge_id, organization_id),
        )
        row = cur.fetchone()
        if not row:
            raise LookupError("知识不存在")
        snapshot = {
            key: value for key, value in row.items()
            if key not in {"embedding"}
        }
        cur.execute(
            """
            INSERT INTO knowledge_version(knowledge_id,version,snapshot,created_by)
            VALUES (%s,%s,%s,%s)
            ON CONFLICT (knowledge_id,version) DO NOTHING
            """,
            (knowledge_id, row["version"], json.dumps(snapshot, default=str),
             account_id),
        )
        cur.execute(
            """
            UPDATE knowledge_unit
            SET status='published', approved_by=%s, published_at=now(),
                updated_at=now()
            WHERE id=%s
            RETURNING id, version, published_at
            """,
            (account_id, knowledge_id),
        )
        result = cur.fetchone()
        conn.commit()
    return result


def archive_knowledge(organization_id: int, knowledge_id: int) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE knowledge_unit SET status='archived', updated_at=now()
            WHERE id=%s AND organization_id=%s
            """,
            (knowledge_id, organization_id),
        )
        if cur.rowcount == 0:
            raise LookupError("知识不存在")
        conn.commit()


def import_faq_text(
    organization_id: int,
    account_id: int,
    content: str,
) -> dict:
    """Import simple Q/A blocks separated by blank lines."""
    blocks = [block.strip() for block in content.split("\n---\n") if block.strip()]
    created = []
    for index, block in enumerate(blocks, start=1):
        question = ""
        answer = ""
        mode = ""
        for line in block.splitlines():
            if line.strip().lower() in {"q:", "q：", "问:", "问："}:
                mode = "q"
                continue
            if line.strip().lower() in {"a:", "a：", "答:", "答："}:
                mode = "a"
                continue
            if mode == "q":
                question += line + "\n"
            elif mode == "a":
                answer += line + "\n"
        if not question.strip() or not answer.strip():
            continue
        created.append(create_knowledge(
            organization_id, account_id,
            kind="faq",
            title=question.strip()[:100],
            question=question.strip(),
            answer=answer.strip(),
            risk_level="low",
            source_ref=f"文本导入 #{index}",
        ))
    return {"created": created, "count": len(created)}


def list_gaps(organization_id: int, status: str = "open") -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT g.id, g.question, g.reason, g.status, g.priority,
                   g.created_at, g.interaction_id, a.display_name AS assignee_name,
                   g.resolution_knowledge_id, g.resolution_note
            FROM knowledge_gap g
            LEFT JOIN account a ON a.id=g.assignee_id
            WHERE g.organization_id=%s AND (%s='' OR g.status=%s)
            ORDER BY g.priority DESC, g.created_at DESC
            LIMIT 200
            """,
            (organization_id, status, status),
        )
        return cur.fetchall()


def ignore_gap(organization_id: int, gap_id: int, note: str) -> None:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE knowledge_gap
            SET status='ignored', resolution_note=%s, resolved_at=now()
            WHERE id=%s AND organization_id=%s
            """,
            (note, gap_id, organization_id),
        )
        if cur.rowcount == 0:
            raise LookupError("知识缺口不存在")
        conn.commit()


def resolve_gap_with_knowledge(
    organization_id: int,
    account_id: int,
    gap_id: int,
    *,
    answer: str,
    title: str,
    source_ref: str,
) -> dict:
    knowledge = create_knowledge(
        organization_id, account_id,
        kind="faq", title=title, question="", answer=answer,
        risk_level="medium", source_ref=source_ref,
    )
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE knowledge_gap g
            SET status='resolved', resolution_knowledge_id=%s,
                resolution_note='已创建知识草稿', resolved_at=now()
            FROM interaction i
            WHERE g.id=%s AND g.organization_id=%s
              AND i.id=g.interaction_id
            RETURNING g.question
            """,
            (knowledge["id"], gap_id, organization_id),
        )
        row = cur.fetchone()
        if row:
            cur.execute(
                "UPDATE knowledge_unit SET question=%s WHERE id=%s",
                (row["question"], knowledge["id"]),
            )
        conn.commit()
    return knowledge

