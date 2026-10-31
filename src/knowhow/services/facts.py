"""Product, variant and approved price-fact management."""
from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation

from knowhow.db import connect


def list_facts(organization_id: int) -> list[dict]:
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.id,p.sku,p.name,p.category,p.status,
                   count(DISTINCT v.id)::int AS variants,
                   count(DISTINCT o.id) FILTER (
                     WHERE o.status='published' AND o.valid_until>=CURRENT_DATE
                   )::int AS active_offers
            FROM product p
            LEFT JOIN product_variant v ON v.product_id=p.id
            LEFT JOIN price_offer o ON o.variant_id=v.id
            WHERE p.organization_id=%s
            GROUP BY p.id ORDER BY p.updated_at DESC
            LIMIT 500
            """,
            (organization_id,),
        )
        return cur.fetchall()


def import_facts_csv(
    organization_id: int,
    account_id: int,
    content: bytes,
) -> dict:
    text = content.decode("utf-8-sig", errors="replace")
    rows = list(csv.DictReader(io.StringIO(text)))
    errors = []
    imported = 0
    with connect() as conn, conn.cursor() as cur:
        for line, row in enumerate(rows, start=2):
            normalized = {
                (key or "").strip().lower(): (value or "").strip()
                for key, value in row.items()
            }
            sku = normalized.get("sku") or normalized.get("货号") or normalized.get("型号")
            name = normalized.get("name") or normalized.get("品名") or normalized.get("产品名称")
            price_text = (
                normalized.get("unit_price")
                or normalized.get("price")
                or normalized.get("价格")
            )
            valid_until_text = (
                normalized.get("valid_until")
                or normalized.get("有效期")
            )
            if not sku or not name or not price_text or not valid_until_text:
                errors.append({"row": line, "error": "sku、名称、价格、有效期为必填"})
                continue
            try:
                price = Decimal(price_text)
                valid_until = date.fromisoformat(valid_until_text)
            except (InvalidOperation, ValueError):
                errors.append({"row": line, "error": "价格或有效期格式错误"})
                continue
            variant_code = normalized.get("variant_code") or "default"
            variant_name = normalized.get("variant_name") or "标准款"
            currency = (normalized.get("currency") or "USD").upper()
            try:
                moq = int(normalized["moq"]) if normalized.get("moq") else None
                lead_time = (
                    int(normalized["lead_time_days"])
                    if normalized.get("lead_time_days") else None
                )
            except ValueError:
                errors.append({"row": line, "error": "MOQ 或交期必须是整数"})
                continue
            cur.execute(
                """
                INSERT INTO product
                  (organization_id,sku,name,category,status,created_by)
                VALUES (%s,%s,%s,%s,'published',%s)
                ON CONFLICT (organization_id,sku) DO UPDATE
                SET name=EXCLUDED.name,category=EXCLUDED.category,
                    status='published',updated_at=now()
                RETURNING id
                """,
                (
                    organization_id, sku, name,
                    normalized.get("category", ""), account_id,
                ),
            )
            product_id = cur.fetchone()["id"]
            cur.execute(
                """
                INSERT INTO product_variant
                  (organization_id,product_id,code,name,attributes)
                VALUES (%s,%s,%s,%s,'{}'::jsonb)
                ON CONFLICT (product_id,code) DO UPDATE
                SET name=EXCLUDED.name
                RETURNING id
                """,
                (organization_id, product_id, variant_code, variant_name),
            )
            variant_id = cur.fetchone()["id"]
            cur.execute(
                """
                UPDATE price_offer SET status='expired'
                WHERE variant_id=%s AND status='published'
                """,
                (variant_id,),
            )
            cur.execute(
                """
                INSERT INTO price_offer
                  (organization_id,variant_id,currency,unit_price,moq,
                   lead_time_days,valid_until,status,created_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,'published',%s)
                """,
                (
                    organization_id, variant_id, currency, price, moq,
                    lead_time, valid_until, account_id,
                ),
            )
            imported += 1
        conn.commit()
    return {"imported": imported, "errors": errors, "total": len(rows)}


def lookup_facts(organization_id: int, question: str) -> list[dict]:
    """Find current approved offer by SKU or product name appearing in question."""
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT p.sku,p.name,v.code AS variant_code,v.name AS variant_name,
                   o.currency,o.unit_price::text,o.moq,o.lead_time_days,
                   o.valid_until
            FROM product p
            JOIN product_variant v ON v.product_id=p.id
            JOIN price_offer o ON o.variant_id=v.id
            WHERE p.organization_id=%s AND p.status='published'
              AND o.status='published' AND o.valid_until>=CURRENT_DATE
              AND (
                position(lower(p.sku) in lower(%s))>0
                OR position(lower(p.name) in lower(%s))>0
              )
            ORDER BY o.valid_until DESC
            LIMIT 5
            """,
            (organization_id, question, question),
        )
        return cur.fetchall()

