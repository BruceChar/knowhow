"""Grounded sales-answer orchestration."""
from __future__ import annotations

import json

from openai import OpenAI

from knowhow.db import connect
from knowhow.domain import (
    AnswerDecision,
    DYNAMIC_FACT_TERMS,
    HIGH_RISK_TERMS,
    answer_decision,
    detect_language,
    is_company_product_question,
    language_mismatch,
)
from knowhow.services.retrieval import retrieve_with_meta
from knowhow.services.facts import (
    format_product_overview,
    lookup_facts,
    product_overview,
)
from knowhow.settings import settings


def _active_rules(organization_id: int) -> list[str]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT rule FROM answer_rule
            WHERE organization_id=%s AND status='active'
            ORDER BY priority, id
            """,
            (organization_id,),
        )
        return [row["rule"] for row in cur.fetchall()]


def _extractive_answer(knowledge: list[dict]) -> str:
    primary = knowledge[0]
    refs = ", ".join(f"知识 #{item['id']}" for item in knowledge[:3])
    return f"{primary['answer'].strip()}\n\n[依据: {refs}]"


def _draft(question: str, knowledge: list[dict], rules: list[str]) -> tuple[str, str]:
    if not settings.llm_api_key or not settings.llm_model:
        answer = _extractive_answer(knowledge)
        return answer, "extractive"
    context = "\n\n".join(
        f"[知识 #{item['id']}] {item['title']}\n{item['answer']}"
        for item in knowledge
    )
    rule_text = "\n".join(f"- {rule}" for rule in rules)
    client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url or None,
    )
    response = client.chat.completions.create(
        model=settings.llm_model,
        temperature=0.2,
        messages=[
            {
                "role": "system",
                "content": (
                    "你是外贸销售助理。只能依据知识内容回答，不得编造。"
                    "输出客户可读的回复，并在末尾列出来源知识编号。\n"
                    f"必须遵守的公司规则：\n{rule_text}"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"客户问题：{question}\n\n可用知识：\n{context}\n\n"
                    f"语言：{detect_language(question)}"
                ),
            },
        ],
    )
    return response.choices[0].message.content.strip(), settings.llm_model


def _fact_answer(rows: list[dict]) -> str:
    parts = []
    for row in rows:
        suffix = f"（{row['variant_name']}）" if row["variant_name"] else ""
        line = (
            f"{row['name']} {row['sku']}{suffix}："
            f"{row['currency']} {row['unit_price']}"
        )
        extras = []
        if row["moq"]:
            extras.append(f"MOQ {row['moq']}")
        if row["lead_time_days"]:
            extras.append(f"交期 {row['lead_time_days']} 天")
        if row["valid_until"]:
            extras.append(f"有效期至 {row['valid_until']}")
        if extras:
            line += "；" + "；".join(extras)
        parts.append(line)
    parts.append("以上来自已发布产品报价，发送客户前请业务员确认。")
    return "\n".join(parts)


def answer_question(
    organization_id: int,
    account_id: int,
    question: str,
    *,
    channel: str = "web",
) -> dict:
    overview = (
        product_overview(organization_id)
        if is_company_product_question(question) else None
    )
    if not overview or not overview["categories"]:
        overview = None
        knowledge, retrieval_meta = retrieve_with_meta(
            organization_id, account_id, question,
        )
        decision = answer_decision(question, knowledge)
        fact_rows = lookup_facts(organization_id, question) if any(
            term in question.lower() for term in DYNAMIC_FACT_TERMS
        ) else []
    else:
        knowledge = []
        retrieval_meta = {
            "mode": "structured",
            "product_count": overview["product_count"],
            "category_count": len(overview["categories"]),
        }
        decision = AnswerDecision(
            "draft", "根据当前产品目录汇总生成", "low",
        )
        fact_rows = []
    if fact_rows and not any(term in question.lower() for term in HIGH_RISK_TERMS):
        decision = AnswerDecision(
            "draft", "价格和交期来自已发布产品事实，发送前需业务员确认", "medium",
        )
    answer = ""
    model = "none"
    citations = [
        {
            "id": item["id"],
            "title": item["title"],
            "kind": item["kind"],
            "risk_level": item["risk_level"],
        }
        for item in knowledge
    ]
    if overview:
        answer = format_product_overview(
            overview, detect_language(question),
        )
        model = "product-catalog-summary"
        citations = [{
            "id": "product-catalog",
            "title": f"产品目录汇总 · {overview['product_count']} 个 SKU",
            "kind": "product_catalog",
            "risk_level": "low",
        }]
    elif fact_rows and decision.status != "escalated":
        answer = _fact_answer(fact_rows)
        model = "approved-fact-template"
        decision = AnswerDecision(
            "draft", "价格和交期来自已发布产品事实，发送前需业务员确认", "medium",
        )
    elif knowledge and decision.status != "escalated":
        rules = _active_rules(organization_id)
        answer, model = _draft(question, knowledge, rules)
        if language_mismatch(question, answer):
            decision = AnswerDecision(
                "escalated",
                "回答语言与客户问题不一致，需要人工转写确认",
                "medium",
            )
            answer = (
                "当前知识依据与客户语言不一致，暂时不能直接对客发送。"
                "请人工确认并补充中文知识后再回复。"
            )
            model = "language-guard"
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO interaction
              (organization_id,account_id,question,ai_answer,status,channel,
               language,citations,escalated_reason)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (
                organization_id, account_id, question, answer,
                decision.status, channel, detect_language(question),
                json.dumps(citations, ensure_ascii=False),
                "" if decision.status != "escalated" else decision.reason,
            ),
        )
        interaction_id = cur.fetchone()["id"]
        if decision.status == "escalated":
            cur.execute(
                """
                INSERT INTO knowledge_gap
                  (organization_id,interaction_id,question,reason,priority)
                VALUES (%s,%s,%s,%s,%s)
                """,
                (
                    organization_id, interaction_id, question, decision.reason,
                    90 if decision.risk == "high" else 60,
                ),
            )
        conn.commit()
    return {
        "interaction_id": interaction_id,
        "answer": answer,
        "status": decision.status,
        "reason": decision.reason,
        "risk": decision.risk,
        "model": model,
        "citations": citations,
        "facts": fact_rows,
        "retrieval": retrieval_meta,
    }


def update_interaction(
    organization_id: int,
    account_id: int,
    interaction_id: int,
    *,
    final_answer: str,
    status: str,
) -> dict:
    from knowhow.domain import edit_ratio

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT ai_answer, account_id FROM interaction
            WHERE id=%s AND organization_id=%s AND account_id=%s FOR UPDATE
            """,
            (interaction_id, organization_id, account_id),
        )
        row = cur.fetchone()
        if not row:
            raise LookupError("交互记录不存在")
        ratio = edit_ratio(row["ai_answer"], final_answer)
        cur.execute(
            """
            UPDATE interaction
            SET final_answer=%s, status=%s, edit_distance=%s,
                sent_at=CASE WHEN %s='sent' THEN now() ELSE sent_at END,
                updated_at=now()
            WHERE id=%s
            RETURNING id,status,final_answer,edit_distance,sent_at
            """,
            (final_answer, status, ratio, status, interaction_id),
        )
        result = cur.fetchone()
        if status == "sent" and row["ai_answer"] and ratio >= 0.35:
            cur.execute(
                """
                INSERT INTO knowledge_gap
                  (organization_id,interaction_id,question,reason,priority)
                SELECT %s,%s,question,%s,40 FROM interaction WHERE id=%s
                """,
                (
                    organization_id, interaction_id,
                    "AI 草稿修改幅度较大，需要沉淀更准确的知识",
                    interaction_id,
                ),
            )
        conn.commit()
    return result


def update_outcome(
    organization_id: int,
    account_id: int,
    interaction_id: int,
    *,
    outcome: str,
    value: float | None,
    note: str,
) -> dict:
    if outcome not in {"unknown", "following", "won", "lost"}:
        raise ValueError("客户结果不支持")
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            UPDATE interaction
            SET outcome=%s,outcome_value=%s,outcome_note=%s,
                outcome_updated_at=now(),updated_at=now()
            WHERE id=%s AND organization_id=%s AND account_id=%s
            RETURNING id,outcome,outcome_value,outcome_note,outcome_updated_at
            """,
            (
                outcome, value, note, interaction_id, organization_id,
                account_id,
            ),
        )
        result = cur.fetchone()
        if not result:
            raise LookupError("交互记录不存在")
        conn.commit()
    return result

