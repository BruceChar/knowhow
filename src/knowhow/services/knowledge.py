"""Knowledge authoring, publishing and gap resolution."""
from __future__ import annotations

import json
from typing import Any

from knowhow.db import connect
from knowhow.services.embeddings import (
    EmbeddingUnavailable,
    embed_text,
    model_name,
    pgvector_literal,
)
import re


def _normalize_key(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip().lower())


def _assert_visible(cur, organization_id: int, account_id: int, knowledge_id: int):
    cur.execute(
        """
        SELECT 1
        FROM knowledge_unit k
        WHERE k.id=%s AND k.organization_id=%s
          AND (
            EXISTS (
              SELECT 1 FROM account a
              JOIN access_role r ON r.id=a.role_id
              WHERE a.id=%s AND 'data.all'=ANY(r.permissions)
            )
            OR k.scope='organization'
            OR (k.scope='team' AND k.team_id IN (
              SELECT team_id FROM team_member WHERE account_id=%s
            ))
            OR (k.scope='private' AND k.owner_id=%s)
          )
        """,
        (
            knowledge_id, organization_id, account_id, account_id, account_id,
        ),
    )
    if not cur.fetchone():
        raise LookupError("知识不存在或无权访问")


def _assert_team_scope(cur, organization_id: int, account_id: int, team_id: int):
    cur.execute(
        """
        SELECT 1
        FROM team t
        WHERE t.id=%s AND t.organization_id=%s
          AND (
            EXISTS (
              SELECT 1 FROM account a
              JOIN access_role r ON r.id=a.role_id
              WHERE a.id=%s AND 'data.all'=ANY(r.permissions)
            )
            OR EXISTS (
              SELECT 1 FROM team_member tm
              WHERE tm.team_id=t.id AND tm.account_id=%s
            )
          )
        """,
        (team_id, organization_id, account_id, account_id),
    )
    if not cur.fetchone():
        raise ValueError("团队不存在或无权使用")


def _assert_knowledge_permission(
    cur,
    organization_id: int,
    account_id: int,
    knowledge_id: int,
    permission: str,
):
    cur.execute(
        """
        SELECT k.scope,
               EXISTS (
                 SELECT 1 FROM account a
                 JOIN access_role r ON r.id=a.role_id
                 WHERE a.id=%s AND %s=ANY(r.permissions)
               ) AS global_allowed,
               EXISTS (
                 SELECT 1 FROM team_member tm
                 JOIN access_role r ON r.id=tm.role_id
                 WHERE tm.account_id=%s AND tm.team_id=k.team_id
                   AND %s=ANY(r.permissions)
               ) AS team_allowed
        FROM knowledge_unit k
        WHERE k.id=%s AND k.organization_id=%s
        """,
        (
            account_id, permission, account_id, permission,
            knowledge_id, organization_id,
        ),
    )
    row = cur.fetchone()
    if not row:
        raise LookupError("知识不存在")
    if not (row["global_allowed"] or (
        row["scope"] == "team" and row["team_allowed"]
    )):
        raise PermissionError(f"缺少团队或公司级权限：{permission}")


def list_knowledge(
    organization_id: int,
    account_id: int,
    *,
    status: str | None = None,
    kind: str | None = None,
    query: str = "",
) -> list[dict]:
    where = ["k.organization_id=%s"]
    params: list[Any] = [
        organization_id, account_id, account_id, account_id,
    ]
    where.append("""
        (
          EXISTS (
            SELECT 1 FROM account a
            JOIN access_role r ON r.id=a.role_id
            WHERE a.id=%s AND 'data.all'=ANY(r.permissions)
          )
          OR k.scope='organization'
          OR (k.scope='team' AND k.team_id IN (
            SELECT team_id FROM team_member WHERE account_id=%s
          ))
          OR (k.scope='private' AND k.owner_id=%s)
        )
    """)
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
                   k.scope, k.team_id, t.name AS team_name,
                   k.embedding_model, k.embedding_updated_at,
                   k.published_at, k.created_at, k.updated_at
            FROM knowledge_unit k
            LEFT JOIN account a ON a.id=k.owner_id
            LEFT JOIN team t ON t.id=k.team_id
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
    scope: str = "organization",
    team_id: int | None = None,
    owner_id: int | None = None,
    metadata: dict | None = None,
) -> dict:
    if scope not in {"organization", "team", "private"}:
        raise ValueError("知识可见范围不正确")
    if scope == "team" and not team_id:
        raise ValueError("团队知识必须指定团队")
    with connect() as conn, conn.cursor() as cur:
        responsible_id = owner_id or account_id
        cur.execute(
            "SELECT 1 FROM account WHERE id=%s AND organization_id=%s",
            (responsible_id, organization_id),
        )
        if not cur.fetchone():
            raise ValueError("知识负责人不存在")
        if team_id:
            _assert_team_scope(cur, organization_id, account_id, team_id)
        cur.execute(
            """
            SELECT id,title,answer FROM knowledge_unit
            WHERE organization_id=%s
              AND (
                regexp_replace(lower(question),'\\s+','','g')=%s
                OR regexp_replace(lower(title),'\\s+','','g')=%s
              )
            LIMIT 5
            """,
            (organization_id, _normalize_key(question), _normalize_key(title)),
        )
        conflicts = cur.fetchall()
        if any(
            _normalize_key(item["answer"]) == _normalize_key(answer)
            for item in conflicts
        ):
            raise ValueError("知识库已存在相同问答")
        cur.execute(
            """
            INSERT INTO knowledge_unit
              (organization_id,kind,title,question,answer,status,risk_level,
               owner_id,source_ref,valid_until,created_by,metadata,scope,team_id)
            VALUES (%s,%s,%s,%s,%s,'draft',%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (
                organization_id, kind, title.strip(), question.strip(),
                answer.strip(), risk_level, responsible_id, source_ref.strip(),
                valid_until or None, account_id, json.dumps(metadata or {}),
                scope, team_id if scope == "team" else None,
            ),
        )
        knowledge_id = cur.fetchone()["id"]
        conn.commit()
    return {
        "id": knowledge_id,
        "status": "draft",
        "potential_conflicts": conflicts,
    }


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
    scope: str,
    team_id: int | None,
    account_id: int,
    owner_id: int | None = None,
) -> dict:
    with connect() as conn, conn.cursor() as cur:
        _assert_visible(cur, organization_id, account_id, knowledge_id)
        _assert_knowledge_permission(
            cur, organization_id, account_id, knowledge_id, "knowledge.edit",
        )
        if scope == "team" and not team_id:
            raise ValueError("团队知识必须指定团队")
        if team_id:
            _assert_team_scope(cur, organization_id, account_id, team_id)
        responsible_id = owner_id or account_id
        cur.execute(
            "SELECT 1 FROM account WHERE id=%s AND organization_id=%s",
            (responsible_id, organization_id),
        )
        if not cur.fetchone():
            raise ValueError("知识负责人不存在")
        cur.execute(
            """
            UPDATE knowledge_unit
            SET title=%s, question=%s, answer=%s, risk_level=%s,
                source_ref=%s, valid_until=%s, owner_id=%s, version=version+1,
                scope=%s, team_id=%s, status='draft', updated_at=now()
            WHERE id=%s AND organization_id=%s
            RETURNING id, version
            """,
            (
                title.strip(), question.strip(), answer.strip(), risk_level,
                source_ref.strip(), valid_until or None,
                responsible_id, scope, team_id if scope == "team" else None,
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
        _assert_visible(cur, organization_id, account_id, knowledge_id)
        _assert_knowledge_permission(
            cur, organization_id, account_id, knowledge_id, "knowledge.publish",
        )
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
        embedding = None
        embedding_error = ""
        try:
            vector = embed_text(
                "\n".join((
                    row["title"] or "", row["question"] or "",
                    row["answer"] or "",
                ))
            )
            embedding = pgvector_literal(vector)
        except EmbeddingUnavailable as exc:
            embedding_error = str(exc)
        cur.execute(
            """
            UPDATE knowledge_unit
            SET status='published', approved_by=%s, published_at=now(),
                embedding=%s::vector,
                embedding_model=CASE WHEN %s::text IS NULL THEN NULL ELSE %s END,
                embedding_updated_at=CASE
                  WHEN %s::text IS NULL THEN embedding_updated_at ELSE now()
                END,
                updated_at=now()
            WHERE id=%s
            RETURNING id, version, published_at,
                      (embedding IS NOT NULL) AS embedding_ready
            """,
            (
                account_id, embedding, embedding, model_name(),
                embedding, knowledge_id,
            ),
        )
        result = cur.fetchone()
        if embedding_error:
            result["embedding_error"] = embedding_error
        conn.commit()
    return result


def rebuild_embedding(organization_id: int, account_id: int, knowledge_id: int) -> dict:
    with connect() as conn, conn.cursor() as cur:
        _assert_knowledge_permission(
            cur, organization_id, account_id, knowledge_id, "knowledge.edit",
        )
        cur.execute(
            """
            SELECT title,question,answer FROM knowledge_unit
            WHERE id=%s AND organization_id=%s
            """,
            (knowledge_id, organization_id),
        )
        row = cur.fetchone()
        if not row:
            raise LookupError("知识不存在")
        try:
            vector = embed_text(
                "\n".join((
                    row["title"] or "", row["question"] or "",
                    row["answer"] or "",
                ))
            )
        except EmbeddingUnavailable as exc:
            raise RuntimeError(f"Embedding 不可用：{exc}") from exc
        cur.execute(
            """
            UPDATE knowledge_unit
            SET embedding=%s::vector, embedding_model=%s,
                embedding_updated_at=now()
            WHERE id=%s
            RETURNING id,embedding_model,embedding_updated_at
            """,
            (pgvector_literal(vector), model_name(), knowledge_id),
        )
        result = cur.fetchone()
        conn.commit()
    return result


def archive_knowledge(
    organization_id: int,
    account_id: int,
    knowledge_id: int,
) -> None:
    with connect() as conn, conn.cursor() as cur:
        _assert_visible(cur, organization_id, account_id, knowledge_id)
        _assert_knowledge_permission(
            cur, organization_id, account_id, knowledge_id, "knowledge.publish",
        )
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


def list_owners(organization_id: int) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT a.id,a.display_name,r.name AS role_name
            FROM account a JOIN access_role r ON r.id=a.role_id
            WHERE a.organization_id=%s AND a.enabled
            ORDER BY a.id
            """,
            (organization_id,),
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

