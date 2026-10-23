"""门禁引擎：输入（问题+知识卡+上下文）→ 输出动作级与理由。
红线用代码规则，不依赖 LLM 自觉——红线零容忍是产品承诺。"""
import re

RED_LINES = [                                    # 红线正则，命中即 D
    r"违?法|走私|洗钱|偷税|漏税",                  # 合规红线
    r"低价|底价|成本价|出厂价",                    # 价格红线（禁止透露成本结构）
    r"保证|承诺.*(绝对|一定|百分百)|终身保修",     # 过度承诺
]
SENSITIVE_FIELDS = ["定金", "预付款", "合同", "独家", "专利", "认证"]

def decide(question: str, cards: list[dict], context: dict = None) -> dict:
    context = context or {}
    for pat in RED_LINES:                         # 1) 红线优先
        if re.search(pat, question, re.I):
            return {"level": "D", "reason": f"命中红线: {pat}", "need_tool": False}

    if not cards:                                 # 2) 无知识可引
        return {"level": "D", "reason": "无已批准知识可引用，禁止编造", "need_tool": False}

    top = cards[0]
    risk = top.get("risk_level", "low")
    if risk == "high":                            # 3) 高风险升 C
        return {"level": "C", "reason": "命中高风险知识卡", "need_tool": True}
    if any(f in question for f in SENSITIVE_FIELDS):
        return {"level": "C", "reason": "敏感字段需人工", "need_tool": False}

    need_price = ("价格" in question or "多少钱" in question or "报价" in question)
    if need_price:                                # 4) 涉及动态报价走 B+工具
        return {"level": "B", "reason": "涉及报价，需业务员确认", "need_tool": True}
    if risk == "medium" or context.get("new_customer"):
        return {"level": "B", "reason": "中风险/新客户，需确认", "need_tool": False}
    return {"level": "A", "reason": "低风险且证据完整", "need_tool": False}