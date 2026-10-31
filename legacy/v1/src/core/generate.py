"""Generation layer with provider injection and deterministic citation IDs."""
import json, os, time
from src.providers.llm_provider import get_llm_provider
from src.core import policies as answer_policies


def _extractive_answer(question: str, cards: list[dict],
                       tool_result: dict | None,
                       policies: list[dict] | None = None) -> str:
    """Offline-safe draft built only from approved evidence, never guessed."""
    primary = cards[0]
    if (answer_policies.has_policy(policies, "language_mirror")
            and answer_policies.language_mismatch(
                question, primary.get("answer", ""))):
        return answer_policies.language_guard_answer(cards, tool_result)
    lines = [primary["answer"].strip()]
    if tool_result:
        values = tool_result.get("values", {})
        useful = []
        for key, label in (
            ("currency", "币种"), ("unit_price", "单价"), ("moq", "MOQ"),
            ("lead_time_days", "交期（天）"), ("valid_until", "有效至"),
        ):
            if values.get(key) not in (None, ""):
                useful.append(f"{label}: {values[key]}")
        if useful:
            lines.extend(["", "已审批报价事实：" + "；".join(useful)])
    sources = ",".join(primary.get("sources", [])) or "无证据编号"
    lines.extend(["", f"[来源: 知识卡{primary['id']}, 证据编号 {sources}]"])
    return "\n".join(lines)


def draft_reply(question: str, cards: list[dict],
                tool_result: dict | None = None,
                action_level: str = "B",
                backend: str | None = None,
                policies: list[dict] | None = None) -> dict:
    ctx = "\n\n".join(
        f"[知识卡{c['id']}][{c.get('risk_level','low')}] {c['question']}\n"
        f"→ {c['answer']}\n(证据: {','.join(c.get('sources',[]))})"
        for c in cards)
    sys = ("你是外贸业务助理。只依据提供的知识卡与工具结果回答，禁止编造。"
           "回答末尾必须列出来源编号，格式：[来源: 知识卡ID, 证据编号]。"
           f"本次动作等级：{action_level}（A自动/B需确认/C升级/D阻断）。")
    policy_text = answer_policies.policy_instruction_text(policies)
    if policy_text:
        sys += "\n必须遵守以下已审核回答规则：\n" + policy_text
    t0 = time.time()
    backend_name = backend or os.environ.get("LLM_BACKEND", "openai")
    has_key = bool(os.environ.get("LLM_API_KEY"))
    if backend_name == "openai" and not has_key and os.environ.get(
        "LLM_REQUIRED", "0"
    ) != "1":
        answer = _extractive_answer(
            question, cards, tool_result, policies,
        )
        model = (
            "grounded-language-guard"
            if answer.startswith("当前只检索到英文知识依据")
            else "grounded-extractive"
        )
        return {
            "answer": answer,
            "citations": [c["id"] for c in cards],
            "latency_ms": int((time.time() - t0) * 1000),
            "model": model,
            "action_level": action_level,
            "requires_human_review": model == "grounded-language-guard",
        }
    provider = get_llm_provider(backend)
    answer = provider.chat(
        [{"role": "system", "content": sys},
         {"role": "user", "content":
          f"客户问题：{question}\n\n{ctx}\n\n"
          f"业务工具结果：{json.dumps(tool_result or {}, ensure_ascii=False, default=str)}"}],
        temperature=0.2,
    )
    model = getattr(provider, "model", provider.name)
    requires_human_review = False
    if (answer_policies.has_policy(policies, "language_mirror")
            and answer_policies.language_mismatch(question, answer)):
        answer = answer_policies.language_guard_answer(cards, tool_result)
        model = f"{model}-language-guard"
        requires_human_review = True
    return {"answer": answer,
            "citations": [c["id"] for c in cards],
            "latency_ms": int((time.time()-t0)*1000),
            "model": model,
            "action_level": action_level,
            "requires_human_review": requires_human_review}

def audit(conn, inquiry_id: str, channel: str, question: str, payload: dict):
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO audit_log (inquiry_id, channel, question, answer, citations,
                                   action_level, decision, latency_ms, model)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """, (inquiry_id, channel, question, payload["answer"],
              json.dumps(payload["citations"], ensure_ascii=False),
              payload["action_level"], payload.get("decision","draft"),
              payload["latency_ms"], payload["model"]))
    conn.commit()
