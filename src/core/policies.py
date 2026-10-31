"""Versioned answer policies shared by LLM and deterministic templates."""
from __future__ import annotations

import re
from typing import Any

_CJK = re.compile(r"[\u3400-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")


def load_active_policies(conn, applies_to: str = "all") -> list[dict]:
    """Load active policies with stable priority ordering.

    Policies are instructions, not knowledge evidence. They can constrain
    language, format and escalation, but cannot supply business facts.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id, name, rule, policy_kind, applies_to, priority
            FROM answer_policy
            WHERE status='active' AND (applies_to='all' OR applies_to=%s)
            ORDER BY priority, id
            """,
            (applies_to,),
        )
        rows = cur.fetchall()
    cols = ("id", "name", "rule", "policy_kind", "applies_to", "priority")
    return [dict(zip(cols, row)) for row in rows]


def policy_instruction_text(policies: list[dict] | None) -> str:
    """Render policies for an LLM system prompt."""
    if not policies:
        return ""
    lines = [
        f"- [{policy.get('policy_kind', 'instruction')}] "
        f"{policy.get('rule', '')}"
        for policy in policies
        if policy.get("rule")
    ]
    return "\n".join(lines)


def has_policy(policies: list[dict] | None, kind: str) -> bool:
    return any(policy.get("policy_kind") == kind for policy in policies or [])


def language_mismatch(question: str, answer: str) -> bool:
    """Detect an English-heavy answer to a Chinese customer question."""
    if not question or not answer or not _CJK.search(question):
        return False
    cjk_count = len(_CJK.findall(answer))
    latin_count = len(_LATIN.findall(answer))
    return latin_count >= 15 and latin_count > cjk_count * 3


def language_guard_answer(
    cards: list[dict],
    tool_result: dict[str, Any] | None = None,
) -> str:
    """Offline-safe response when evidence cannot be mirrored in Chinese."""
    refs = []
    for card in cards:
        sources = ",".join(card.get("sources", [])) or "无证据编号"
        refs.append(f"知识卡{card['id']}（{sources}）")
    lines = [
        "当前只检索到英文知识依据，暂时没有可直接对客使用的中文答复。",
        "请人工根据引用内容转写并确认后再发送，或补充该问题的标准中文知识。",
    ]
    if tool_result:
        lines.append("业务工具结果仍需按当前报价和交期规则人工确认。")
    if refs:
        lines.extend(["", "引用依据：" + "；".join(refs)])
    return "\n".join(lines)
