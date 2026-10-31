"""CSV, text and chat-export import pipeline."""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from pathlib import Path

from knowhow.db import connect

QUESTION_COLUMNS = {
    "question", "q", "问题", "客户问题", "客户提问", "customer question",
}
ANSWER_COLUMNS = {
    "answer", "a", "答案", "回复", "标准答案", "agent reply", "response",
}
TITLE_COLUMNS = {"title", "标题", "名称"}
SPEAKER_COLUMNS = {"speaker", "role", "sender", "说话人", "角色"}
MESSAGE_COLUMNS = {"message", "content", "text", "消息", "内容"}
CUSTOMER_ROLES = {"customer", "buyer", "客户", "顾客"}
AGENT_ROLES = {"agent", "sales", "seller", "客服", "业务员", "销售"}


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip().lower())


def _header(value: str) -> str:
    return value.replace("\ufeff", "").strip().lower()


def _find_column(headers: list[str], aliases: set[str]) -> str | None:
    normalized = {_header(header): header for header in headers}
    for alias in aliases:
        if alias in normalized:
            return normalized[alias]
    return None


def _chat_pairs(messages: list[tuple[str, str]]) -> list[dict]:
    pairs = []
    pending = ""
    for speaker, message in messages:
        role = speaker.strip().lower()
        if role in CUSTOMER_ROLES or any(
            word in role for word in ("客户", "customer", "buyer")
        ):
            pending = message.strip()
        elif (
            pending
            and (
                role in AGENT_ROLES
                or any(word in role for word in ("客服", "业务", "sales", "seller", "agent"))
            )
        ):
            pairs.append({
                "question": pending,
                "answer": message.strip(),
                "title": pending[:100],
            })
            pending = ""
    return pairs


def _parse_csv(content: str) -> tuple[list[dict], dict]:
    reader = csv.DictReader(io.StringIO(content))
    headers = reader.fieldnames or []
    question_col = _find_column(headers, QUESTION_COLUMNS)
    answer_col = _find_column(headers, ANSWER_COLUMNS)
    title_col = _find_column(headers, TITLE_COLUMNS)
    speaker_col = _find_column(headers, SPEAKER_COLUMNS)
    message_col = _find_column(headers, MESSAGE_COLUMNS)
    mapping = {
        "question": question_col,
        "answer": answer_col,
        "title": title_col,
        "speaker": speaker_col,
        "message": message_col,
    }
    rows = list(reader)
    candidates = []
    if question_col and answer_col:
        for row in rows:
            candidates.append({
                "question": (row.get(question_col) or "").strip(),
                "answer": (row.get(answer_col) or "").strip(),
                "title": (
                    (row.get(title_col) if title_col else "")
                    or (row.get(question_col) or "")
                ).strip()[:100],
            })
    elif speaker_col and message_col:
        candidates = _chat_pairs([
            (
                (row.get(speaker_col) or "").strip(),
                (row.get(message_col) or "").strip(),
            )
            for row in rows
        ])
    return candidates, mapping


def _parse_text(content: str) -> tuple[list[dict], dict]:
    if "Q:" in content or "Q：" in content:
        blocks = [block.strip() for block in re.split(r"\n---\n", content) if block.strip()]
        candidates = []
        for block in blocks:
            question = re.search(r"(?:^|\n)[Qq][:：]\s*(.+?)(?=\n[Aa][:：]|\Z)", block, re.S)
            answer = re.search(r"(?:^|\n)[Aa][:：]\s*(.+)", block, re.S)
            if question and answer:
                q = question.group(1).strip()
                candidates.append({
                    "question": q, "answer": answer.group(1).strip(),
                    "title": q[:100],
                })
        return candidates, {"format": "qa_blocks"}
    messages = []
    for line in content.splitlines():
        match = re.match(r"^\s*([^:：]{1,40})[:：]\s*(.+)$", line)
        if match:
            messages.append((match.group(1).strip(), match.group(2).strip()))
    return _chat_pairs(messages), {
        "format": "chat_lines",
        "messages": len(messages),
    }


def _exists(cur, organization_id: int, question: str, title: str) -> bool:
    cur.execute(
        """
        SELECT 1 FROM knowledge_unit
        WHERE organization_id=%s
          AND (
            regexp_replace(lower(question),'\\s+','','g')=%s
            OR regexp_replace(lower(title),'\\s+','','g')=%s
          )
        LIMIT 1
        """,
        (organization_id, _norm(question), _norm(title)),
    )
    return bool(cur.fetchone())


def preview_import(
    organization_id: int,
    account_id: int,
    *,
    filename: str,
    content: bytes,
    source_type: str = "auto",
) -> dict:
    text = content.decode("utf-8-sig", errors="replace")
    suffix = Path(filename).suffix.lower()
    if suffix == ".csv":
        candidates, mapping = _parse_csv(text)
    else:
        candidates, mapping = _parse_text(text)
    preview_rows = []
    issues = []
    duplicates = 0
    ready = 0
    seen = set()
    with connect() as conn, conn.cursor() as cur:
        for index, row in enumerate(candidates, start=1):
            question = row.get("question", "").strip()
            answer = row.get("answer", "").strip()
            title = (row.get("title") or question).strip()[:200]
            status = "ready"
            error = ""
            key = _norm(question or title)
            if not question or not answer:
                status = "error"
                error = "问题或答案为空"
            elif key in seen:
                status = "duplicate"
                error = "文件内重复"
            elif _exists(cur, organization_id, question, title):
                status = "duplicate"
                error = "知识库已存在相同问题或标题"
            seen.add(key)
            if status == "ready":
                ready += 1
            elif status == "duplicate":
                duplicates += 1
            else:
                issues.append({"row": index, "error": error})
            preview_rows.append({
                "row": index, "title": title, "question": question,
                "answer": answer, "status": status, "error": error,
            })
        checksum = hashlib.sha256(content).hexdigest()
        cur.execute(
            """
            INSERT INTO import_batch
              (organization_id,source_name,source_type,sha256,status,mapping,
               preview_rows,issues,total_rows,ready_rows,duplicate_rows,
               error_rows,created_by)
            VALUES (%s,%s,%s,%s,'preview',%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id,created_at
            """,
            (
                organization_id, filename, source_type or suffix.lstrip("."),
                checksum, json.dumps(mapping, ensure_ascii=False),
                json.dumps(preview_rows, ensure_ascii=False),
                json.dumps(issues, ensure_ascii=False), len(preview_rows),
                ready, duplicates, len(issues), account_id,
            ),
        )
        batch = cur.fetchone()
        conn.commit()
    return {
        **batch,
        "source_name": filename,
        "mapping": mapping,
        "total_rows": len(preview_rows),
        "ready_rows": ready,
        "duplicate_rows": duplicates,
        "error_rows": len(issues),
        "preview": preview_rows[:20],
        "issues": issues[:30],
    }


def execute_import(organization_id: int, account_id: int, batch_id: int) -> dict:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT * FROM import_batch
            WHERE id=%s AND organization_id=%s FOR UPDATE
            """,
            (batch_id, organization_id),
        )
        batch = cur.fetchone()
        if not batch:
            raise LookupError("导入批次不存在")
        if batch["status"] == "completed":
            raise ValueError("该批次已经执行")
        created = 0
        for row in batch["preview_rows"]:
            if row["status"] != "ready":
                continue
            if _exists(cur, organization_id, row["question"], row["title"]):
                continue
            cur.execute(
                """
                INSERT INTO knowledge_unit
                  (organization_id,kind,title,question,answer,status,risk_level,
                   owner_id,source_ref,created_by,scope)
                VALUES (%s,'faq',%s,%s,%s,'draft','medium',%s,%s,%s,
                        'organization')
                """,
                (
                    organization_id, row["title"], row["question"], row["answer"],
                    account_id, f"导入批次 #{batch_id}", account_id,
                ),
            )
            created += 1
        cur.execute(
            """
            UPDATE import_batch
            SET status='completed',created_count=%s,executed_at=now()
            WHERE id=%s
            """,
            (created, batch_id),
        )
        conn.commit()
    return {"batch_id": batch_id, "created": created, "status": "completed"}


def list_imports(organization_id: int) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT id,source_name,source_type,status,total_rows,ready_rows,
                   duplicate_rows,error_rows,created_count,created_at,
                   executed_at
            FROM import_batch WHERE organization_id=%s
            ORDER BY id DESC LIMIT 50
            """,
            (organization_id,),
        )
        return cur.fetchall()

