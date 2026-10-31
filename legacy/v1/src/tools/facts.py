from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

import psycopg


@dataclass(frozen=True)
class FactResult:
    available: bool
    source: str
    values: dict = field(default_factory=dict)
    reason: str = ""


class FactTool(Protocol):
    def resolve(self, question: str, context: dict | None = None) -> FactResult: ...


_SKU_PATTERN = re.compile(r"\b(JC[A-Z0-9][A-Z0-9._/-]{1,40})\b", re.I)


class OfferFactTool:
    """Read a current approved Offer snapshot.

    This is deliberately a read-only adapter. A production implementation can
    replace it with the customer's quote/ERP API while preserving the contract.
    """

    def __init__(self, dsn: str):
        self.dsn = dsn

    def resolve(self, question: str, context: dict | None = None) -> FactResult:
        context = context or {}
        sku = str(context.get("sku_code") or "").strip()
        if not sku:
            match = _SKU_PATTERN.search(question)
            sku = match.group(1).upper() if match else ""
        if not sku:
            return FactResult(False, "offer_db", reason="缺少可识别的 SKU，无法查询当前报价")

        with psycopg.connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("""
                SELECT o.id, s.sku_code, v.variant_code, o.currency, o.unit_price,
                       o.moq, o.lead_time_days, o.freight_mode, o.valid_until
                FROM sku s
                JOIN variant v ON v.sku_id = s.id
                JOIN offer o ON o.variant_id = v.id
                WHERE upper(s.sku_code) = upper(%s)
                  AND s.status = 'approved'
                  AND o.status = 'approved'
                  AND o.valid_until >= CURRENT_DATE
                ORDER BY o.valid_until DESC, o.id DESC
                LIMIT 1
            """, (sku,))
            row = cur.fetchone()
        if not row:
            return FactResult(False, "offer_db", reason=f"未找到 {sku} 的有效已批准报价")
        keys = ["offer_id", "sku_code", "variant_code", "currency", "unit_price", "moq",
                "lead_time_days", "freight_mode", "valid_until"]
        return FactResult(True, "offer_db", dict(zip(keys, row)))
