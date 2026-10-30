"""Deterministic action policy for customer-facing knowledge responses."""
import re

RED_LINES = [
    r"违?法|走私|洗钱|偷税|漏税",
    r"个人账户|私人账户|purpose account|different account",
    r"第三方退款|退给.{0,8}(代理|第三方)|refund.{0,20}(agent|third party)",
    r"低报|少报货值|under.?declar|lower.{0,12}(invoice|value)",
    r"客户名单|customer list|passport|护照|验证码|verification code",
    r"复制.{0,12}(logo|商标)|copy.{0,12}(logo|trademark)|侵权",
    r"保证|承诺.*(绝对|一定|百分百)|终身保修|guarantee.{0,20}(absolute|always)",
    r"成本价|出厂价|最低价.*(直接|马上)|rock.?bottom price",
]

SENSITIVE_FIELDS = [
    "定金", "预付款", "合同", "独家", "专利", "认证",
    "deposit", "payment", "contract", "exclusive", "patent",
    "certificate", "warranty", "claim", "refund",
]

DYNAMIC_FACT_TERMS = (
    "价格", "多少钱", "报价", "moq", "起订量", "交期", "库存", "现货",
    "price", "quote", "lead time", "delivery", "shipping",
    "inventory", "stock",
)

def decide(question: str, cards: list[dict], context: dict = None) -> dict:
    context = context or {}
    for pat in RED_LINES:                         # 1) 红线优先
        if re.search(pat, question, re.I):
            return {"level": "D", "reason": f"命中红线: {pat}", "need_tool": False}

    need_dynamic_fact = any(term in question.lower() for term in DYNAMIC_FACT_TERMS)
    if not cards and need_dynamic_fact:
        return {"level": "B", "reason": "动态事实需查询业务接口", "need_tool": True}
    if not cards:
        return {"level": "C", "reason": "无已批准知识可引用，转人工核实", "need_tool": False}

    top = cards[0]
    risk = top.get("risk_level", "low")
    if risk == "high":                            # 3) 高风险升 C
        return {"level": "C", "reason": "命中高风险知识卡", "need_tool": True}
    if any(f in question for f in SENSITIVE_FIELDS):
        return {"level": "C", "reason": "敏感字段需人工", "need_tool": False}

    if need_dynamic_fact:
        return {"level": "B", "reason": "涉及报价，需业务员确认", "need_tool": True}
    if risk == "medium" or context.get("new_customer"):
        return {"level": "B", "reason": "中风险/新客户，需确认", "need_tool": False}
    return {"level": "A", "reason": "低风险且证据完整", "need_tool": False}
