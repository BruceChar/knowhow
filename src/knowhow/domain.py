"""Domain constants and small business-rule helpers."""
from __future__ import annotations

import re
from dataclasses import dataclass

ROLES = {"owner", "steward", "sales", "admin"}
KNOWLEDGE_KINDS = {"faq", "product", "policy", "playbook", "case", "sop"}
KNOWLEDGE_STATUSES = {"draft", "published", "archived"}
INTERACTION_STATUSES = {"draft", "copied", "sent", "escalated"}

HIGH_RISK_TERMS = (
    "保证", "承诺", "赔付", "退款", "认证", "独家", "合同", "发票金额",
    "guarantee", "warranty", "refund", "certificate", "exclusive",
)
DYNAMIC_FACT_TERMS = (
    "价格", "报价", "多少钱", "交期", "库存", "现货", "起订量", "moq",
    "price", "quote", "lead time", "stock", "inventory",
)
_CJK = re.compile(r"[\u3400-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]")


@dataclass(frozen=True)
class AnswerDecision:
    status: str
    reason: str
    risk: str


def detect_language(text: str) -> str:
    return "zh" if _CJK.search(text or "") else "en"


def language_mismatch(question: str, answer: str) -> bool:
    if not _CJK.search(question or "") or not answer:
        return False
    cjk = len(_CJK.findall(answer))
    latin = len(_LATIN.findall(answer))
    return latin >= 15 and latin > cjk * 3


def is_company_product_question(question: str) -> bool:
    """Detect questions that should be answered from the product catalog."""
    lowered = (question or "").strip().lower()
    chinese_intents = (
        "主营产品", "主要产品", "主营业务", "主营什么", "产品有哪些",
        "产品有什么", "卖什么", "做什么产品", "产品线", "经营范围",
    )
    english_intents = (
        "what products", "which products", "product range", "product line",
        "what do you sell", "what does your company do", "main products",
        "main product categories",
    )
    return any(term in lowered for term in chinese_intents) or any(
        term in lowered for term in english_intents
    )


def answer_decision(question: str, knowledge: list[dict]) -> AnswerDecision:
    lowered = question.lower()
    if not knowledge:
        return AnswerDecision("escalated", "没有匹配的已发布知识", "high")
    if any(term in lowered for term in HIGH_RISK_TERMS):
        return AnswerDecision(
            "escalated",
            "问题涉及认证、承诺或合同，需要负责人确认",
            "high",
        )
    if any(term in lowered for term in DYNAMIC_FACT_TERMS):
        risk = max(
            (item.get("risk_level", "low") for item in knowledge[:3]),
            key=lambda value: {"low": 0, "medium": 1, "high": 2}.get(value, 0),
        )
        return AnswerDecision(
            "draft",
            "涉及动态业务事实，发送前必须核对报价或交期",
            risk if risk != "low" else "medium",
        )
    return AnswerDecision("draft", "基于已发布知识生成", "low")


def edit_ratio(original: str, final: str) -> float:
    """Return a simple normalized character edit ratio for analytics."""
    import difflib

    if not original and not final:
        return 0.0
    return round(1.0 - difflib.SequenceMatcher(None, original, final).ratio(), 4)

