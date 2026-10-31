"""Real-company-question practice and readiness scoring."""
from __future__ import annotations

import json

from knowhow.db import connect
from knowhow.services.retrieval import tokens


def score_response(reference: str, response: str) -> tuple[int, list[str], list[str]]:
    reference_tokens = tokens(reference)
    response_tokens = tokens(response)
    matched = sorted(reference_tokens & response_tokens)
    missing = sorted(reference_tokens - response_tokens)
    score = round(len(matched) * 100 / len(reference_tokens)) if reference_tokens else 0
    return score, matched, missing


def quiz(organization_id: int, account_id: int, limit: int = 5) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT k.id,k.question,k.kind,k.risk_level
            FROM knowledge_unit k
            WHERE k.organization_id=%s AND k.status='published'
              AND k.kind='faq' AND k.question!=''
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
            ORDER BY random()
            LIMIT %s
            """,
            (organization_id, account_id, account_id, account_id, limit),
        )
        items = cur.fetchall()
    return {"items": items}


def submit_attempt(
    organization_id: int,
    account_id: int,
    knowledge_id: int,
    response: str,
) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id,question,answer
            FROM knowledge_unit
            WHERE id=%s AND organization_id=%s AND status='published'
              AND (
                EXISTS (
                  SELECT 1 FROM account a
                  JOIN access_role r ON r.id=a.role_id
                  WHERE a.id=%s AND 'data.all'=ANY(r.permissions)
                )
                OR scope='organization'
                OR (scope='team' AND team_id IN (
                  SELECT team_id FROM team_member WHERE account_id=%s
                ))
                OR (scope='private' AND owner_id=%s)
              )
            """,
            (
                knowledge_id, organization_id, account_id, account_id, account_id,
            ),
        )
        knowledge = cur.fetchone()
        if not knowledge:
            raise LookupError("训练题不存在或无权访问")
        score, matched, missing = score_response(knowledge["answer"], response)
        feedback = {
            "matched": matched[:12],
            "missing": missing[:12],
            "reference_answer": knowledge["answer"],
            "passed": score >= 60,
        }
        cur.execute(
            """
            INSERT INTO training_attempt
              (organization_id,account_id,knowledge_id,response,score,feedback)
            VALUES (%s,%s,%s,%s,%s,%s)
            RETURNING id,score,feedback,created_at
            """,
            (
                organization_id, account_id, knowledge_id, response, score,
                json.dumps(feedback, ensure_ascii=False),
            ),
        )
        attempt = cur.fetchone()
        conn.commit()
    return attempt


def readiness(organization_id: int, account_id: int) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            WITH recent AS (
              SELECT score, knowledge_id
              FROM training_attempt
              WHERE organization_id=%s AND account_id=%s
              ORDER BY created_at DESC LIMIT 20
            )
            SELECT count(*) AS attempts,
                   COALESCE(round(avg(score)),0) AS average_score,
                   count(DISTINCT knowledge_id)
                     FILTER (WHERE score>=60) AS passed_questions,
                   COALESCE(max(score),0) AS best_score
            FROM recent
            """,
            (organization_id, account_id),
        )
        return cur.fetchone()

