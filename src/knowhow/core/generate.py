"""生成层：把检索到的知识卡 + 工具结果 + 门禁决策，交给模型起草，并写审计。"""
import json, time, uuid
from openai import OpenAI

def make_client(cfg):
    return OpenAI(base_url=cfg["llm_base_url"], api_key=cfg["llm_api_key"])

def draft_reply(client, cfg, question: str, cards: list[dict],
                tool_result: str = "", action_level: str = "B") -> dict:
    ctx = "\n\n".join(
        f"[知识卡{c['id']}][{c.get('risk_level','low')}] {c['question']}\n→ {c['answer']}\n(证据: {','.join(c.get('sources',[]))})"
        for c in cards)
    sys = ("你是外贸业务助理。只依据提供的知识卡与工具结果回答，禁止编造。"
           "回答末尾必须列出来源编号，格式：[来源: 知识卡ID, 证据编号]。"
           f"本次动作等级：{action_level}（A自动/B需确认/C升级/D阻断）。")
    t0 = time.time()
    resp = client.chat.completions.create(
        model=cfg["llm_model"], temperature=0.2,
        messages=[{"role":"system","content":sys},
                  {"role":"user","content":f"客户问题：{question}\n\n{ctx}\n\n工具结果：{tool_result or '无'}（仅作参考，不作为承诺）"}])
    return {"answer": resp.choices[0].message.content,
            "citations": [c["id"] for c in cards],
            "latency_ms": int((time.time()-t0)*1000),
            "model": cfg["llm_model"], "action_level": action_level}

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