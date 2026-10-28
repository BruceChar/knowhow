from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass

import psycopg

from src.auth.rbac import DATA_PERMISSION_VISIBLE, User
from src.core import gate, generate, search
from src.tools.facts import FactResult, FactTool, OfferFactTool


def _render_fact_answer(values: dict) -> str:
    price = values.get("unit_price")
    currency = values.get("currency", "")
    parts = [f"当前已审批报价：{currency} {price}"]
    labels = (
        ("moq", "MOQ"),
        ("lead_time_days", "交期"),
        ("freight_mode", "运输方式"),
        ("valid_until", "报价有效至"),
    )
    extra = [f"{label}：{values[key]}" for key, label in labels
             if values.get(key) not in (None, "")]
    if extra:
        parts.append("；".join(extra))
    parts.append("此价格来自已审批报价快照，发送客户前请由业务员确认。")
    if values.get("offer_id"):
        parts.append(f"[来源: 报价事实 OFFER-{values['offer_id']}]")
    return "\n".join(parts)


@dataclass
class AskResponse:
    inquiry_id: str
    answer: str
    citations: list[int]
    citation_details: list[dict]
    action: str
    reason: str
    customer_ready: bool
    needs_human_review: bool
    missing_slots: list[str]
    tool_result: dict | None
    latency_ms: int
    model: str


def ask_question(
    conn: psycopg.Connection,
    user: User,
    question: str,
    *,
    channel: str = "web",
    customer_id: str = "",
    sku_code: str = "",
    llm_backend: str | None = None,
    fact_tool: FactTool | None = None,
    fact_dsn: str | None = None,
) -> AskResponse:
    started = time.time()
    inquiry_id = uuid.uuid4().hex[:16]
    permissions = DATA_PERMISSION_VISIBLE.get(user.role, ["public"])
    cards = search.hybrid_search(
        conn,
        question,
        k=5,
        permissions=permissions,
        customer_ready=True,
        backend=None,
    )
    decision = gate.decide(
        question,
        cards,
        {"customer_id": customer_id, "sku_code": sku_code, "channel": channel},
    )

    tool_payload = None
    citation_ids = [int(card["id"]) for card in cards]
    missing_slots: list[str] = []
    answer = ""
    model = ""
    needs_human_review = decision["level"] in {"B", "C"}

    if decision.get("need_tool"):
        try:
            tool = fact_tool or OfferFactTool(fact_dsn or str(conn.info.dsn))
            result = tool.resolve(question, {"sku_code": sku_code})
        except Exception as exc:
            result = FactResult(
                False, "offer_db", {},
                f"业务事实接口不可用：{exc}",
            )
        if not result.available:
            missing_slots = [result.reason]
            decision = {**decision, "level": "C", "reason": result.reason}
            needs_human_review = True
        else:
            tool_payload = {"source": result.source, "values": result.values}
            if not cards:
                answer = _render_fact_answer(result.values)
                model = "approved-fact-template"

    if cards and decision["level"] != "D" and (
        not decision.get("need_tool") or tool_payload
    ):
        try:
            payload = generate.draft_reply(
                question,
                cards,
                tool_payload,
                decision["level"],
                backend=llm_backend,
            )
            answer = payload["answer"]
            citation_ids = payload["citations"]
            model = payload["model"]
        except Exception as exc:
            decision = {**decision, "level": "C", "reason": f"模型生成失败：{exc}"}
            needs_human_review = True

    latency_ms = int((time.time() - started) * 1000)
    payload = {
        "answer": answer,
        "citations": citation_ids,
        "latency_ms": latency_ms,
        "model": model,
        "action_level": decision["level"],
        "decision": "draft" if decision["level"] in {"B", "C"} else "auto",
    }
    if answer:
        generate.audit(conn, inquiry_id, channel, question, payload)
    else:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO audit_log
                  (inquiry_id, channel, question, answer, citations,
                   action_level, decision, latency_ms, model)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                inquiry_id, channel, question, "", json.dumps(citation_ids),
                decision["level"], "block" if decision["level"] == "D" else "escalate",
                latency_ms, model,
            ))
        conn.commit()

    return AskResponse(
        inquiry_id=inquiry_id,
        answer=answer,
        citations=citation_ids,
        citation_details=[
            {
                "id": card["id"],
                "question": card.get("question", ""),
                "answer": card.get("answer", ""),
                "category": card.get("category"),
                "risk_level": card.get("risk_level"),
                "action_level": card.get("action_level"),
                "sources": card.get("sources", []),
            }
            for card in cards if card["id"] in set(citation_ids)
        ],
        action=decision["level"],
        reason=decision["reason"],
        customer_ready=bool(cards) and decision["level"] in {"A", "B"},
        needs_human_review=needs_human_review,
        missing_slots=missing_slots,
        tool_result=tool_payload,
        latency_ms=latency_ms,
        model=model,
    )
