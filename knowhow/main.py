from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import psycopg, uuid
from knowhow.core import search, generate, gate

app = FastAPI(title="外贸知识服务", version="0.1.0")
DSN = "postgresql://rag:rag_dev_2026@localhost:5433/rag"
CFG = {"llm_base_url": "https://api.deepseek.com/v1", "llm_api_key": "",
       "llm_model": "deepseek-chat"}

class Inquiry(BaseModel):
    question: str
    channel: str = "email"
    customer_id: str = ""

@app.post("/v1/inquiry")
def handle_inquiry(q: Inquiry):
    inquiry_id = uuid.uuid4().hex[:16]
    with psycopg.connect(DSN) as conn:
        cards = search.hybrid_search(conn, q.question, k=5)          # 步骤3 检索
        action = gate.decide(q.question, cards)                       # 步骤5 门禁
        if action["level"] == "D":
            raise HTTPException(403, "该问题超出自动服务范围，请转人工")
        tool_result = _tool_lookup(q.question) if action.get("need_tool") else ""
        client = generate.make_client(CFG)
        payload = generate.draft_reply(client, CFG, q.question, cards,
                                       tool_result, action["level"])
        payload["decision"] = "auto" if action["level"] == "A" else "draft"
        generate.audit(conn, inquiry_id, q.channel, q.question, payload)  # 步骤6 审计
    return {"inquiry_id": inquiry_id, "answer": payload["answer"],
            "citations": payload["citations"], "decision": payload["decision"]}

def _tool_lookup(question: str) -> str:
    """动态事实走业务接口（报价/库存/交期），不进 RAG。MVP 先返回占位。"""
    return ""  # TODO: 接入 ERP / 报价服务 API