"""话术库 JSON → raw_data 留档 + faq 表（防腐层驱动版）。

embedding 由 EmbeddingProvider 注入（local bge-m3 / API 可替换），
批次与原始留档记录到 import_batch / raw_data（与 ingest_facts 一致）。

用法：
  uv run python app/ingest_faq.py data/raw/faq.json
  EMBEDDING_BACKEND=api uv run python app/ingest_faq.py data/raw/faq.json
"""
import argparse, json, hashlib, os, sys
from pathlib import Path

import psycopg
from pgvector.psycopg import register_vector

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))
from src.providers.embedding_provider import get_embedding_provider

DEFAULT_DSN = "postgresql://rag:rag_dev_2026@localhost:5433/rag"


def load_faq(entries: list[dict], dsn: str, source_file: str,
             backend: str | None = None) -> dict:
    # 1) 向量化（防腐层：上层不感知 local/api）
    provider = get_embedding_provider(backend)
    texts = [e["question"] + "\n" + e["answer"] for e in entries]
    print(f"[ingest_faq] 编码 {len(texts)} 条 (backend={provider.name})...")
    embs = provider.embed(texts)
    assert len(embs) == len(entries), "向量数量与条目数量不一致！"

    sha = hashlib.sha256(Path(source_file).read_bytes()).hexdigest()
    loaded, updated = 0, 0
    with psycopg.connect(dsn) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            # 批次
            cur.execute("""
                INSERT INTO import_batch
                  (source_file, file_sha256, file_type, source_kind, status, started_by)
                VALUES (%s,%s,'json','faq','running',%s)
                RETURNING id
            """, (os.path.basename(source_file), sha,
                  os.environ.get("USER", "cli")))
            batch_id = cur.fetchone()[0]

            for idx, (e, emb) in enumerate(zip(entries, embs), start=1):
                # 原始留档
                cur.execute("""
                    INSERT INTO raw_data (batch_id, sheet_name, row_no, raw, verdict)
                    VALUES (%s,'faq.json',%s,%s,'pending')
                """, (batch_id, idx, json.dumps(e, ensure_ascii=False)))

                cur.execute("""
                    INSERT INTO faq (question, answer, category, tags, sources,
                                     risk_level, action_level, status, embedding)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,'draft',%s)
                    ON CONFLICT (question) DO UPDATE SET
                        answer=EXCLUDED.answer,
                        category=EXCLUDED.category,
                        tags=EXCLUDED.tags,
                        sources=EXCLUDED.sources,
                        risk_level=EXCLUDED.risk_level,
                        action_level=EXCLUDED.action_level,
                        customer_ready=false,
                        status='draft',
                        embedding=EXCLUDED.embedding
                    RETURNING (xmax = 0) AS inserted
                """, (e["question"], e["answer"], e.get("module"),
                      [e.get("id")], [e.get("id")],
                      e.get("risk_level", "low"), e.get("action_level", "B"), emb))
                row = cur.fetchone()
                if row and row[0]:
                    loaded += 1
                    cur.execute("""UPDATE raw_data SET verdict='loaded'
                                   WHERE id=(SELECT max(id) FROM raw_data WHERE batch_id=%s)""",
                                (batch_id,))
                else:
                    updated += 1
                    cur.execute("""UPDATE raw_data SET verdict='loaded'
                                   WHERE id=(SELECT max(id) FROM raw_data WHERE batch_id=%s)""",
                                (batch_id,))

            cur.execute("""
                UPDATE import_batch SET total_rows=%s, loaded_rows=%s, bad_rows=0,
                  status='done', finished_at=now() WHERE id=%s
            """, (len(entries), len(entries), batch_id))
        conn.commit()

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM faq WHERE embedding IS NOT NULL")
            total = cur.fetchone()[0]
    return {"batch_id": batch_id, "loaded": loaded, "updated": updated,
            "faq_with_embedding": total}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--backend", default=os.environ.get("EMBEDDING_BACKEND"))
    a = ap.parse_args()
    with open(a.path, encoding="utf-8") as f:
        data = json.load(f)
    print(json.dumps(load_faq(data, a.dsn, a.path, a.backend),
                     ensure_ascii=False, indent=2))
