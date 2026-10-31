"""Copy the recovered V1 FAQ and product catalog into the active V2 database."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from knowhow.services.embeddings import EmbeddingUnavailable, embed_texts, model_name
from knowhow.settings import settings


MODULE_NAMES = {
    "M01": "开场与基础信息",
    "M02": "目录与市场",
    "M03": "报价与 MOQ",
    "M04": "定制与品牌",
    "M05": "样品",
    "M06": "认证与验厂",
    "M07": "物流与包装",
    "M08": "交期",
    "M09": "付款",
    "M10": "议价与犹豫",
    "M11": "质量与质保",
    "M12": "异常询盘",
    "M13": "极简询盘",
    "M14": "客户身份与渠道",
    "M15": "型号与兼容",
    "M16": "选品推荐",
    "M17": "报价构成",
    "M18": "混柜",
    "M19": "设计稿",
    "M20": "产前样",
    "M21": "工厂透明度",
    "M22": "质检",
    "M23": "合规文件",
    "M24": "装运",
    "M25": "单证",
    "M26": "订单差异",
    "M27": "生产进度",
    "M28": "客诉",
    "M29": "返单",
    "M30": "素材",
    "M31": "展会拜访",
    "M32": "项目结束",
    "M33": "风险话术",
    "M34": "产品功能",
    "M35": "产品细节",
}


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").strip().lower())


def _clean_question(question: str) -> str:
    return re.sub(r"\s*（系统默认第一句）\s*$", "", question.strip())


def _product_text(product: dict) -> str:
    names = " / ".join(filter(None, (product["name_zh"], product["name_en"])))
    lines = [
        f"产品：{names or product['sku_code']}",
        f"SKU：{product['sku_code']}",
        f"分类：{product['category']}",
    ]
    spec = product.get("spec") or {}
    if spec:
        lines.append("规格参数：")
        for key, value in spec.items():
            if value not in (None, "", "/"):
                lines.append(f"- {key}：{value}")
    lines.append("当前未迁入有效报价，价格、MOQ 和交期必须以当前报价单或负责人确认结果为准。")
    return "\n".join(lines)


def _evidence_id(
    conn,
    organization_id: int,
    account_id: int,
    backup_path: Path,
    checksum: str,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT id FROM evidence
            WHERE organization_id=%s AND sha256=%s
              AND source_type='legacy_v1_backup'
            LIMIT 1
            """,
            (organization_id, checksum),
        )
        existing = cur.fetchone()
        if existing:
            return existing["id"]
        cur.execute(
            """
            INSERT INTO evidence
              (organization_id,title,source_type,source_ref,content,sha256,
               created_by,scope)
            VALUES (%s,%s,'legacy_v1_backup',%s,%s,%s,%s,'organization')
            RETURNING id
            """,
            (
                organization_id,
                f"V1 备份 · {backup_path.name}",
                str(backup_path),
                "V1 数据库备份；FAQ 与产品图鉴迁移来源。",
                checksum,
                account_id,
            ),
        )
        return cur.fetchone()["id"]


def _batch_id(
    conn,
    organization_id: int,
    account_id: int,
    backup_path: Path,
    checksum: str,
    total_rows: int,
) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO import_batch
              (organization_id,source_name,source_type,sha256,status,mapping,
               preview_rows,issues,total_rows,ready_rows,duplicate_rows,
               error_rows,created_by)
            VALUES (%s,%s,'legacy_v1',%s,'preview',%s,'[]'::jsonb,
                    '[]'::jsonb,%s,0,0,0,%s)
            RETURNING id
            """,
            (
                organization_id,
                backup_path.name,
                checksum,
                json.dumps({
                    "migration": "v1_to_v2",
                    "source": str(backup_path),
                    "tables": ["faq", "sku"],
                    "embedding": model_name(),
                }, ensure_ascii=False),
                total_rows,
                account_id,
            ),
        )
        return cur.fetchone()["id"]


def _existing_keys(conn, organization_id: int) -> tuple[set[str], set[str]]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT title,question FROM knowledge_unit
            WHERE organization_id=%s
            """,
            (organization_id,),
        )
        rows = cur.fetchall()
    return (
        {_norm(row["question"]) for row in rows if row["question"]},
        {_norm(row["title"]) for row in rows if row["title"]},
    )


def import_faqs(
    source,
    target,
    *,
    organization_id: int,
    account_id: int,
    evidence_id: int,
    batch_id: int,
    existing_questions: set[str],
    existing_titles: set[str],
) -> tuple[int, int]:
    with source.cursor() as source_cur:
        source_cur.execute(
            """
            SELECT id,question,answer,category,tags,sources,status,permission,
                   risk_level,action_level,version,approved_at,updated_at,
                   embedding::text AS embedding_text
            FROM faq
            WHERE trim(question)!='' AND trim(answer)!=''
            ORDER BY id
            """
        )
        rows = source_cur.fetchall()

    imported = 0
    duplicates = 0
    with target.cursor() as cur:
        for row in rows:
            question = _clean_question(row["question"])
            title = question[:200]
            question_key = _norm(question)
            title_key = _norm(title)
            if question_key in existing_questions or title_key in existing_titles:
                duplicates += 1
                continue
            module = row["category"] or ""
            metadata = {
                "module_code": module,
                "module_name": MODULE_NAMES.get(module, module),
                "tags": row["tags"] or [],
                "source_refs": row["sources"] or [],
                "legacy": {
                    "source": "v1",
                    "faq_id": row["id"],
                    "migration_batch_id": batch_id,
                    "status": row["status"],
                    "permission": row["permission"],
                    "action_level": row["action_level"],
                    "version": row["version"],
                },
            }
            cur.execute(
                """
                INSERT INTO knowledge_unit
                  (organization_id,kind,title,question,answer,status,risk_level,
                   owner_id,evidence_id,source_ref,version,metadata,
                   embedding,embedding_model,embedding_updated_at,created_by,
                   approved_by,published_at,scope)
                VALUES (%s,'faq',%s,%s,%s,'published',%s,%s,%s,%s,%s,
                        %s,%s::vector,%s,%s,%s,%s,COALESCE(%s,now()),
                        'organization')
                RETURNING id
                """,
                (
                    organization_id, title, question, row["answer"],
                    row["risk_level"], account_id, evidence_id,
                    f"V1 FAQ #{row['id']} · {module}",
                    row["version"], json.dumps(metadata, ensure_ascii=False),
                    row["embedding_text"],
                    "BAAI/bge-m3 (legacy-v1)",
                    row["updated_at"], account_id, account_id,
                    row["approved_at"],
                ),
            )
            existing_questions.add(question_key)
            existing_titles.add(title_key)
            imported += 1
    return imported, duplicates


def import_products(
    source,
    target,
    *,
    organization_id: int,
    account_id: int,
    evidence_id: int,
    batch_id: int,
    existing_questions: set[str],
    existing_titles: set[str],
) -> tuple[int, int, int]:
    with source.cursor() as source_cur:
        source_cur.execute(
            """
            SELECT id,sku_code,category,name_zh,name_en,spec,status,permission,
                   risk_level,version,updated_at
            FROM sku
            WHERE trim(sku_code)!=''
            ORDER BY id
            """
        )
        products = source_cur.fetchall()

    pending = []
    duplicates = 0
    for product in products:
        answer = _product_text(product)
        question = " ".join(filter(None, (
            product["sku_code"], product["name_zh"], product["name_en"],
        )))
        title = f"{product['name_zh'] or product['name_en']} · {product['sku_code']}"
        question_key = _norm(question)
        title_key = _norm(title)
        if question_key in existing_questions or title_key in existing_titles:
            duplicates += 1
            continue
        pending.append({
            **product,
            "question": question,
            "title": title,
            "answer": answer,
        })
        existing_questions.add(question_key)
        existing_titles.add(title_key)

    embedding_error = ""
    embeddings: list[list[float] | None] = []
    try:
        texts = [
            "\n".join((item["title"], item["question"], item["answer"]))
            for item in pending
        ]
        embeddings = embed_texts(texts) if texts else []
    except EmbeddingUnavailable as exc:
        embedding_error = str(exc)
        embeddings = [None] * len(pending)

    imported = 0
    with target.cursor() as cur:
        for index, product in enumerate(pending):
            name = product["name_zh"] or product["name_en"] or product["sku_code"]
            metadata = {
                "module_name": product["category"],
                "legacy": {
                    "source": "v1",
                    "sku_id": product["id"],
                    "migration_batch_id": batch_id,
                    "status": product["status"],
                    "permission": product["permission"],
                    "version": product["version"],
                },
                "spec": product["spec"] or {},
                "name_en": product["name_en"] or "",
                "price_note": "V1 备份未包含有效报价，发送客户前必须使用当前报价单。",
            }
            cur.execute(
                """
                INSERT INTO product
                  (organization_id,sku,name,category,description,status,
                   attributes,created_by)
                VALUES (%s,%s,%s,%s,%s,'published',%s,%s)
                ON CONFLICT (organization_id,sku) DO UPDATE
                SET name=EXCLUDED.name,category=EXCLUDED.category,
                    description=EXCLUDED.description,status='published',
                    attributes=EXCLUDED.attributes,updated_at=now()
                RETURNING id
                """,
                (
                    organization_id, product["sku_code"], name,
                    product["category"],
                    f"V1 产品图鉴产品：{product['name_zh'] or ''} / {product['name_en'] or ''}".strip(),
                    json.dumps(metadata, ensure_ascii=False), account_id,
                ),
            )
            product_id = cur.fetchone()["id"]
            cur.execute(
                """
                INSERT INTO product_variant
                  (organization_id,product_id,code,name,attributes)
                VALUES (%s,%s,'standard','标准款','{}'::jsonb)
                ON CONFLICT (product_id,code) DO UPDATE SET name='标准款'
                """,
                (organization_id, product_id),
            )
            vector = embeddings[index] if embeddings else None
            vector_literal = (
                "[" + ",".join(f"{value:.8f}" for value in vector) + "]"
                if vector else None
            )
            cur.execute(
                """
                INSERT INTO knowledge_unit
                  (organization_id,kind,title,question,answer,status,risk_level,
                   owner_id,evidence_id,source_ref,version,metadata,
                   embedding,embedding_model,embedding_updated_at,created_by,
                   approved_by,published_at,scope)
                VALUES (%s,'product',%s,%s,%s,'published',%s,%s,%s,%s,%s,
                        %s,%s::vector,%s,CASE WHEN %s::text IS NULL
                          THEN NULL ELSE now() END,%s,%s,now(),
                        'organization')
                """,
                (
                    organization_id, product["title"], product["question"],
                    product["answer"], product["risk_level"], account_id,
                    evidence_id, f"V1 SKU #{product['id']} · {product['category']}",
                    product["version"], json.dumps(metadata, ensure_ascii=False),
                    vector_literal,
                    None if vector is None else "BAAI/bge-m3",
                    vector_literal, account_id, account_id,
                ),
            )
            imported += 1
    return imported, duplicates, 1 if embedding_error else 0


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--source-url",
        default="postgresql://knowhow:knowhow_dev@localhost:5433/knowhow_v1_import_20261101",
    )
    parser.add_argument("--target-url", default=settings.database_url)
    parser.add_argument(
        "--backup",
        default="data/backups/knowhow_20261023_195347.dump.gz",
    )
    parser.add_argument("--owner-username", default="owner")
    args = parser.parse_args()

    backup_path = Path(args.backup)
    checksum = hashlib.sha256(backup_path.read_bytes()).hexdigest()
    with (
        psycopg.connect(args.source_url, row_factory=dict_row) as source,
        psycopg.connect(args.target_url, row_factory=dict_row) as target,
    ):
        with target.cursor() as cur:
            cur.execute(
                """
                SELECT a.id,a.organization_id
                FROM account a
                WHERE a.username=%s AND a.enabled
                """,
                (args.owner_username,),
            )
            owner = cur.fetchone()
        if not owner:
            raise SystemExit(f"target owner not found: {args.owner_username}")
        organization_id = owner["organization_id"]
        account_id = owner["id"]
        evidence_id = _evidence_id(
            target, organization_id, account_id, backup_path, checksum,
        )
        with source.cursor() as cur:
            cur.execute("SELECT count(*) AS count FROM faq")
            faq_total = cur.fetchone()["count"]
            cur.execute("SELECT count(*) AS count FROM sku")
            sku_total = cur.fetchone()["count"]
        batch_id = _batch_id(
            target, organization_id, account_id, backup_path, checksum,
            faq_total + sku_total,
        )
        existing_questions, existing_titles = _existing_keys(
            target, organization_id,
        )
        faq_imported, faq_duplicates = import_faqs(
            source, target,
            organization_id=organization_id,
            account_id=account_id,
            evidence_id=evidence_id,
            batch_id=batch_id,
            existing_questions=existing_questions,
            existing_titles=existing_titles,
        )
        product_imported, product_duplicates, embedding_errors = import_products(
            source, target,
            organization_id=organization_id,
            account_id=account_id,
            evidence_id=evidence_id,
            batch_id=batch_id,
            existing_questions=existing_questions,
            existing_titles=existing_titles,
        )
        created = faq_imported + product_imported
        duplicates = faq_duplicates + product_duplicates
        with target.cursor() as cur:
            cur.execute(
                """
                UPDATE import_batch
                SET status='completed',ready_rows=%s,duplicate_rows=%s,
                    created_count=%s,error_rows=%s,executed_at=now(),
                    mapping=mapping || %s::jsonb
                WHERE id=%s
                """,
                (
                    created, duplicates, created, embedding_errors,
                    json.dumps({
                        "faq_imported": faq_imported,
                        "product_imported": product_imported,
                        "faq_duplicates": faq_duplicates,
                        "product_duplicates": product_duplicates,
                        "embedding_errors": embedding_errors,
                    }, ensure_ascii=False),
                    batch_id,
                ),
            )
        target.commit()
    print(json.dumps({
        "batch_id": batch_id,
        "evidence_id": evidence_id,
        "faq_imported": faq_imported,
        "product_imported": product_imported,
        "duplicates_skipped": duplicates,
        "embedding_errors": embedding_errors,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
