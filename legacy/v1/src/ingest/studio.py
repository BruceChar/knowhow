"""Configuration-driven import layer used by the admin Import Studio.

The importer deliberately supports only declarative transforms and allow-listed
target objects. Tenant templates cannot execute SQL or arbitrary Python.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import psycopg

from src.config_loader import get_mapping


SUPPORTED_TARGETS = {"product", "faq"}

FIELD_ALIASES: dict[str, list[str]] = {
    "sku_code": ["型号", "产品型号", "产品编号", "sku", "item", "model", "item no", "item no."],
    "name_zh": ["中文名", "中文名称", "品名", "产品名称", "名称"],
    "name_en": ["英文名", "英文名称", "name", "product name", "english name"],
    "category": ["分类", "类目", "category", "product category"],
    "material": ["材质", "材料", "material"],
    "color": ["颜色", "color", "colour"],
    "size": ["尺寸", "大小", "规格", "size", "dimension"],
    "weight": ["重量", "净重", "产品净重", "克重", "weight", "net weight"],
    "moq": ["起订量", "最小起订量", "moq"],
    "price": ["价格", "单价", "price", "unit price"],
    "currency": ["币种", "货币", "currency"],
    "pack": ["包装", "包装详情", "packing", "package"],
    "carton_size": ["箱规", "外箱尺寸", "carton size"],
    "variant_code": ["变体编码", "SKU", "子SKU", "variant", "variant code"],
    "sku_code": ["型号", "产品型号", "产品编号", "sku", "item", "model",
                 "item no", "item no.", "货号"],
    "valid_until": ["有效期", "报价有效期", "valid until", "valid_until"],
    "lead_time_days": ["交期", "交货期", "发货期", "lead time",
                       "lead time days"],
    "freight_mode": ["运输方式", "贸易条款", "freight", "incoterm"],
    "pack": ["包装", "包装详情", "装箱率", "packing", "package"],
    "question": ["问题", "客户原话", "question", "customer question"],
    "answer": ["答案", "英文回复", "回复", "answer", "response"],
}


class ImportValidationError(ValueError):
    pass


def _clean(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return ""
    text = str(value).strip()
    return "" if text.lower() in {"nan", "none", "null"} else text


def _set_path(target: dict, path: str, value: Any) -> None:
    parts = path.split(".")
    cursor = target
    for part in parts[:-1]:
        cursor = cursor.setdefault(part, {})
    cursor[parts[-1]] = value


def _get_path(source: dict, path: str, default: Any = None) -> Any:
    cursor: Any = source
    for part in path.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return default
        cursor = cursor[part]
    return cursor


def apply_transforms(value: Any, transforms: Iterable[str | dict]) -> Any:
    current = value
    for rule in transforms:
        name = rule if isinstance(rule, str) else rule.get("op", "")
        opts = {} if isinstance(rule, str) else rule
        if name == "trim":
            current = _clean(current)
        elif name == "uppercase":
            current = str(current or "").upper()
        elif name == "lowercase":
            current = str(current or "").lower()
        elif name == "remove_spaces":
            current = re.sub(r"\s+", "", str(current or ""))
        elif name == "number":
            match = re.search(r"[-+]?\d[\d,]*(?:\.\d+)?", str(current or ""))
            text = match.group(0).replace(",", "") if match else ""
            try:
                current = float(text) if text else None
            except ValueError:
                current = None
        elif name == "integer":
            match = re.search(r"[-+]?\d[\d,]*", str(current or ""))
            text = match.group(0).replace(",", "") if match else ""
            try:
                current = int(text) if text else None
            except ValueError:
                current = None
        elif name == "split_list":
            sep = opts.get("separator", ",")
            current = [_clean(x) for x in str(current or "").split(sep) if _clean(x)]
        elif name == "date":
            if current in (None, ""):
                current = None
            else:
                try:
                    current = pd.to_datetime(current, errors="raise").date().isoformat()
                except (TypeError, ValueError) as exc:
                    raise ImportValidationError(f"日期格式无法识别: {current}") from exc
        elif name == "currency":
            text = str(current or "").strip().upper()
            aliases = {"人民币": "CNY", "元": "CNY", "美元": "USD",
                       "欧元": "EUR", "美金": "USD"}
            current = aliases.get(text, text or None)
        elif name == "bool":
            current = str(current or "").strip().lower() in {"1", "true", "yes", "y", "是"}
        else:
            raise ImportValidationError(f"不支持的转换操作: {name}")
    return current


def _is_empty_row(row: dict) -> bool:
    return not any(_clean(v) for v in row.values())


def analyze_source(path: str | Path) -> dict:
    """Return sheet/header/sample information for the mapping wizard."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(str(p))
    suffix = p.suffix.lower()
    result: list[dict] = []
    if suffix in {".xlsx", ".xls"}:
        xl = pd.ExcelFile(p)
        for sheet in xl.sheet_names:
            raw = pd.read_excel(p, sheet_name=sheet, header=None, nrows=20, dtype=str)
            header_index = None
            best = 0
            for i, row in raw.iterrows():
                non_empty = sum(1 for v in row.tolist() if _clean(v))
                if non_empty > best and non_empty >= 2:
                    best = non_empty
                    header_index = i
            if header_index is None:
                result.append({"sheet": sheet, "headers": [], "samples": []})
                continue
            headers = [_clean(v) for v in raw.iloc[header_index].tolist()]
            headers = [h or f"未命名列{i+1}" for i, h in enumerate(headers)]
            body = raw.iloc[header_index + 1: header_index + 6]
            samples = [
                {headers[i]: _clean(v) for i, v in enumerate(row.tolist())}
                for _, row in body.iterrows()
                if any(_clean(v) for v in row.tolist())
            ]
            result.append({
                "sheet": sheet,
                "header_row": int(header_index + 1),
                "headers": headers,
                "samples": samples,
            })
    elif suffix == ".csv":
        df = pd.read_csv(p, dtype=str, nrows=20)
        result.append({
            "sheet": p.stem,
            "header_row": 1,
            "headers": [str(c) for c in df.columns],
            "samples": [
                {str(k): _clean(v) for k, v in row.items()}
                for row in df.head(5).to_dict(orient="records")
            ],
        })
    elif suffix == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("data") or data.get("items") or data.get("cases") or [data]
        if not isinstance(data, list):
            raise ImportValidationError("JSON 顶层必须是数组，或包含 data/items/cases 数组")
        keys = sorted({str(k) for item in data[:20] if isinstance(item, dict) for k in item})
        result.append({
            "sheet": "json",
            "header_row": None,
            "headers": keys,
            "samples": data[:5],
        })
    elif suffix == ".docx":
        from src.ingest.parsers.parse_faq_docx import extract_paragraphs, parse
        items = parse(extract_paragraphs(str(p)))
        keys = ["id", "module", "question", "answer", "context",
                "need_info", "note"]
        result.append({
            "sheet": "faq_docx",
            "header_row": None,
            "headers": keys,
            "samples": items[:5],
        })
    else:
        raise ImportValidationError(f"暂不支持预览的文件类型: {suffix}")
    return {
        "filename": p.name,
        "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
        "sheets": result,
    }


def suggest_mapping(headers: Iterable[str], target: str) -> dict[str, str]:
    normalized = {_clean(h).lower(): _clean(h) for h in headers}
    suggested: dict[str, str] = {}
    canonical = [
        ("sku_code", "sku_code"),
        ("name_zh", "name_zh"),
        ("name_en", "name_en"),
        ("category", "category"),
        ("variant_code", "variant.variant_code"),
        ("material", "variant.attributes.material"),
        ("color", "variant.attributes.color"),
        ("size", "variant.attributes.size"),
        ("weight", "variant.attributes.weight"),
        ("pack", "variant.attributes.pack"),
        ("price", "offer.unit_price"),
        ("currency", "offer.currency"),
        ("moq", "offer.moq"),
        ("lead_time_days", "offer.lead_time_days"),
        ("freight_mode", "offer.freight_mode"),
        ("valid_until", "offer.valid_until"),
    ]
    if target == "faq":
        canonical = [("question", "question"), ("answer", "answer"),
                     ("category", "category")]
    for field, target_path in canonical:
        for alias in FIELD_ALIASES.get(field, []):
            if alias.lower() in normalized:
                suggested[target_path] = normalized[alias.lower()]
                break
    return suggested


def _legacy_template(config: dict, target: str) -> dict:
    fields = []
    mapping = config.get("mapping", {})
    for src, dst in mapping.items():
        fields.append({
            "from": src,
            "to": dst,
            "required": dst in config.get("required", []),
            "transforms": ["trim"],
        })
    for src in config.get("spec_cols", []):
        fields.append({
            "from": src,
            "to": f"spec.{src}",
            "transforms": ["trim"],
        })
    offer_targets = {
        "价格": ("offer.unit_price", ["trim", "number"]),
        "单价": ("offer.unit_price", ["trim", "number"]),
        "样品单价": ("offer.unit_price", ["trim", "number"]),
        "币种": ("offer.currency", ["trim", "currency"]),
        "货币": ("offer.currency", ["trim", "currency"]),
        "起订量": ("offer.moq", ["trim", "integer"]),
        "最小起订量": ("offer.moq", ["trim", "integer"]),
        "交期": ("offer.lead_time_days", ["trim", "integer"]),
        "交货期": ("offer.lead_time_days", ["trim", "integer"]),
        "有效期": ("offer.valid_until", ["trim", "date"]),
        "报价有效期": ("offer.valid_until", ["trim", "date"]),
        "运输方式": ("offer.freight_mode", ["trim"]),
        "贸易条款": ("offer.freight_mode", ["trim"]),
    }
    if config.get("include_offer_fields"):
        for src in config.get("offer_cols", []):
            target, transforms = offer_targets.get(
                _clean(src), (f"offer.ext_json.{_clean(src)}", ["trim"])
            )
            fields.append({
                "from": src,
                "to": target,
                "transforms": transforms,
            })
    return {**config, "target": target, "fields": fields}


def compile_template(config: dict, target: str | None = None,
                     overrides: dict[str, str] | None = None) -> dict:
    target = target or config.get("target") or (
        "faq" if config.get("source_kind") == "faq" else "product"
    )
    if target not in SUPPORTED_TARGETS:
        raise ImportValidationError(f"不支持的目标对象: {target}")
    compiled = _legacy_template(config, target) if "fields" not in config else {
        **config, "target": target
    }
    overrides = overrides or {}
    known_fields = {
        field.get("to", field.get("target", ""))
        for field in compiled.get("fields", [])
    }
    for field_name, source_name in overrides.items():
        if field_name and source_name and field_name not in known_fields:
            compiled.setdefault("fields", []).append({
                "from": source_name,
                "to": field_name,
                "transforms": ["trim"],
            })
    for field in compiled.get("fields", []):
        field_name = field.get("to", field.get("target", ""))
        if field_name in overrides:
            field["from"] = overrides[field_name]
    return compiled


def _records_from_source(path: str | Path, config: dict,
                         selected_sheets: list[str] | None = None
                         ) -> list[tuple[str, int, dict]]:
    p = Path(path)
    suffix = p.suffix.lower()
    reader = config.get("reader", {})
    skip = set(reader.get("skip_sheets", []))
    allowed = set(selected_sheets) if selected_sheets else None
    if allowed is None and reader.get("included_sheets"):
        allowed = set(reader["included_sheets"])
    if suffix in {".xlsx", ".xls"}:
        xl = pd.ExcelFile(p)
        records = []
        for sheet in xl.sheet_names:
            if sheet in skip:
                continue
            if allowed is not None and sheet not in allowed:
                continue
            raw = pd.read_excel(p, sheet_name=sheet, header=None, dtype=str)
            header_index = None
            best = 0
            for i, row in raw.head(reader.get("max_scan_rows", 20)).iterrows():
                non_empty = sum(1 for v in row.tolist() if _clean(v))
                if non_empty > best and non_empty >= 2:
                    best, header_index = non_empty, i
            if header_index is None:
                continue
            headers = [_clean(v) or f"未命名列{i+1}"
                       for i, v in enumerate(raw.iloc[header_index].tolist())]
            df = raw.iloc[header_index + 1:].copy()
            df.columns = headers
            for idx, row in df.iterrows():
                data = {str(k): _clean(v) for k, v in row.to_dict().items()}
                if not _is_empty_row(data):
                    records.append((sheet, int(idx) + 1, data))
        return records
    if suffix == ".csv":
        df = pd.read_csv(p, dtype=str)
        return [(p.stem, int(i) + 2, {str(k): _clean(v) for k, v in row.items()})
                for i, row in df.iterrows()]
    if suffix == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("data") or data.get("items") or [data]
        if not isinstance(data, list):
            raise ImportValidationError("JSON 顶层必须是数组")
        return [("json", i + 1, item) for i, item in enumerate(data)
                if isinstance(item, dict) and not _is_empty_row(item)]
    if suffix == ".docx":
        from src.ingest.parsers.parse_faq_docx import extract_paragraphs, parse
        items = parse(extract_paragraphs(str(p)))
        return [("faq_docx", i + 1, item) for i, item in enumerate(items)
                if not _is_empty_row(item)]
    raise ImportValidationError(f"暂不支持导入的文件类型: {suffix}")


def preview_import(path: str | Path, config: dict, target: str | None = None,
                   overrides: dict[str, str] | None = None,
                   limit: int = 50,
                   selected_sheets: list[str] | None = None) -> dict:
    compiled = compile_template(config, target, overrides)
    rows = _records_from_source(path, compiled, selected_sheets)
    results = []
    errors = []
    seen: set[str] = set()
    for sheet, row_no, source in rows:
        normalized: dict = json.loads(
            json.dumps(compiled.get("defaults", {}), ensure_ascii=False)
        )
        row_errors = []
        for field in compiled.get("fields", []):
            source_name = field.get("from")
            target_name = field.get("to", field.get("target"))
            if not source_name or not target_name:
                continue
            try:
                value = apply_transforms(
                    _get_path(source, source_name, source.get(source_name)),
                    field.get("transforms", []),
                )
            except ImportValidationError as exc:
                row_errors.append(f"字段 {target_name}: {exc}")
                continue
            if field.get("required") and value in (None, "", []):
                row_errors.append(f"缺必填字段 {target_name}")
            if value not in (None, "", []):
                _set_path(normalized, target_name, value)
        match_field = "sku_code" if compiled["target"] == "product" else "question"
        match_value = _clean(_get_path(normalized, match_field, ""))
        variant_code = _clean(
            _get_path(normalized, "variant.variant_code", "")
        )
        seen_key = match_value.lower()
        if compiled["target"] == "product":
            seen_key += "|" + (variant_code.lower() or match_value.lower())
        if not match_value:
            row_errors.append(f"缺少匹配字段 {match_field}")
        elif seen_key in seen:
            row_errors.append(f"批次内重复 {match_field}: {match_value}")
        else:
            seen.add(seen_key)
        if compiled["target"] == "product":
            offer = normalized.get("offer", {})
            if offer.get("unit_price") not in (None, "", []):
                if not offer.get("currency"):
                    row_errors.append("报价缺少币种 currency")
                if not offer.get("valid_until"):
                    row_errors.append("报价缺少有效期 valid_until")
            normalized.setdefault("variant", {})["variant_code"] = (
                normalized.get("variant", {}).get("variant_code")
                or match_value
            )
        item = {
            "sheet": sheet,
            "row_no": row_no,
            "source": source,
            "normalized": normalized,
            "errors": row_errors,
            "status": "error" if row_errors else "ready",
        }
        if len(results) < limit:
            results.append(item)
        if row_errors:
            errors.append(item)
    return {
        "target": compiled["target"],
        "template": compiled.get("config_key") or compiled.get("template_id") or "inline",
        "total_rows": len(rows),
        "ready_rows": len(rows) - len(errors),
        "error_rows": len(errors),
        "sample": results,
    }


def execute_import(path: str | Path, config: dict, dsn: str,
                   target: str | None = None,
                   overrides: dict[str, str] | None = None,
                   actor: str = "system",
                   selected_sheets: list[str] | None = None) -> dict:
    compiled = compile_template(config, target, overrides)
    preview = preview_import(
        path, compiled, limit=1_000_000, selected_sheets=selected_sheets
    )
    if preview["error_rows"]:
        return {"ok": False, **preview}

    p = Path(path)
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    inserted = updated = 0
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        mapping_id = compiled.get("_mapping_id")
        template_version = compiled.get("_template_version")
        mapping_version_id = None
        config_key = compiled.get("config_key") or compiled.get("template_id")
        if config_key and template_version:
            cur.execute("""
                SELECT id FROM mapping_config_version
                WHERE config_key=%s AND version=%s
            """, (config_key, template_version))
            row = cur.fetchone()
            mapping_version_id = row[0] if row else None
        cur.execute("""
            INSERT INTO import_batch
              (source_file, file_sha256, file_type, source_kind, mapping_id,
               mapping_version_id, status, started_by, total_rows)
            VALUES (%s,%s,%s,%s,%s,%s,'running',%s,%s)
            RETURNING id
        """, (p.name, sha, p.suffix.lstrip("."),
              "faq" if compiled["target"] == "faq" else "facts",
              mapping_id, mapping_version_id, actor, preview["total_rows"]))
        batch_id = cur.fetchone()[0]

        for row in preview["sample"]:
            cur.execute("""
                INSERT INTO raw_data
                  (batch_id, sheet_name, row_no, raw, mapped, verdict)
                VALUES (%s,%s,%s,%s::jsonb,%s::jsonb,'loaded')
            """, (batch_id, row["sheet"], row["row_no"],
                  json.dumps(row["source"], ensure_ascii=False, default=str),
                  json.dumps(row["normalized"], ensure_ascii=False, default=str)))
            data = row["normalized"]
            if compiled["target"] == "faq":
                q, a = data.get("question", ""), data.get("answer", "")
                cur.execute("""
                    INSERT INTO faq
                      (question, answer, category, tags, status, permission,
                       risk_level, action_level, customer_ready)
                    VALUES (%s,%s,%s,%s,'draft','internal','low','B',false)
                    ON CONFLICT (question) DO UPDATE
                      SET answer=EXCLUDED.answer, category=EXCLUDED.category,
                          status='draft', embedding=NULL
                    RETURNING (xmax = 0) AS inserted
                """, (q, a, data.get("category"),
                      data.get("tags", [])))
            else:
                sku = data.get("sku_code")
                cur.execute("""
                    INSERT INTO sku
                      (sku_code, category, name_zh, name_en, spec,
                       status, permission, risk_level)
                    VALUES (%s,%s,%s,%s,%s::jsonb,'draft','internal','low')
                    ON CONFLICT (sku_code) DO UPDATE
                      SET category=EXCLUDED.category, name_zh=EXCLUDED.name_zh,
                          name_en=EXCLUDED.name_en, spec=EXCLUDED.spec,
                          status='draft', embedding=NULL
                    RETURNING (xmax = 0) AS inserted
                """, (sku, data.get("category") or "未分类",
                      data.get("name_zh"),
                      data.get("name_en"),
                      json.dumps(data.get("spec", {}), ensure_ascii=False)))
                was_inserted = bool(cur.fetchone()[0])
                cur.execute("SELECT id FROM sku WHERE sku_code=%s", (sku,))
                sku_id = cur.fetchone()[0]
                variant = data.get("variant", {})
                variant_code = variant.get("variant_code") or sku
                attributes = variant.get("attributes", {})
                for key in ("color", "size", "pack", "material", "weight"):
                    if data.get(key) not in (None, "", []):
                        attributes[key] = data[key]
                cur.execute("""
                    INSERT INTO variant
                      (sku_id, variant_code, name_zh, name_en, attributes)
                    VALUES (%s,%s,%s,%s,%s::jsonb)
                    ON CONFLICT (sku_id, variant_code) DO UPDATE
                      SET name_zh=EXCLUDED.name_zh,
                          name_en=EXCLUDED.name_en,
                          attributes=EXCLUDED.attributes
                    RETURNING id
                """, (sku_id, variant_code, data.get("name_zh"),
                      data.get("name_en"),
                      json.dumps(attributes, ensure_ascii=False)))
                variant_id = cur.fetchone()[0]
                offer = data.get("offer", {})
                if offer.get("unit_price") not in (None, "", []):
                    cur.execute("""
                        INSERT INTO offer
                          (variant_id, currency, unit_price, moq,
                           lead_time_days, freight_mode, valid_until, ext_json,
                           status)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'draft')
                    """, (
                        variant_id, offer.get("currency") or "USD",
                        offer["unit_price"], offer.get("moq"),
                        offer.get("lead_time_days"),
                        offer.get("freight_mode"),
                        offer.get("valid_until"),
                        json.dumps(offer.get("ext_json", {}),
                                   ensure_ascii=False),
                    ))
            if compiled["target"] == "faq":
                was_inserted = bool(cur.fetchone()[0])
            inserted += int(was_inserted)
            updated += int(not was_inserted)
        cur.execute("""
            UPDATE import_batch
            SET loaded_rows=%s, status='done', finished_at=now()
            WHERE id=%s
        """, (preview["ready_rows"], batch_id))
        conn.commit()
    return {
        "ok": True, "batch_id": batch_id,
        "loaded": preview["ready_rows"], "inserted": inserted,
        "updated": updated, "errors": 0,
        "target": compiled["target"],
    }
