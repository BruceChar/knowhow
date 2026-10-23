"""产品图鉴 Excel → raw_data 留档 + sku 表（配置驱动版）。

管线：reader（读 Excel）→ mapper（按配置映射）→ validator（校验）→ loader（留档+入库）
映射来自 config/mappings/jiacong_facts.yaml（换企业只换配置，代码零改动）。

用法：uv run python app/ingest_facts.py data/raw/产品资料图鉴.xlsx --config jiacong_facts
"""
import argparse, json, hashlib, os, sys
from pathlib import Path

import pandas as pd
import psycopg

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # 项目根入路径
from src.config_loader import get_mapping

DEFAULT_DSN = "postgresql://rag:rag_dev_2026@localhost:5433/rag"


# ============ reader：读 Excel，按配置动态找表头 ============

def find_header_row(path, sheet_name, detect_col, max_scan):
    head = pd.read_excel(path, sheet_name=sheet_name, header=None,
                         nrows=max_scan, dtype=str)
    for i in range(len(head)):
        row = [str(x) for x in head.iloc[i].tolist()]
        if any(detect_col in x for x in row):
            return i
    return None


def read_sheets(path: str, cfg: dict) -> list[dict]:
    """返回 [(sheet_name, df)]，跳过配置里的说明 sheet。"""
    reader = cfg.get("reader", {})
    detect_col = reader.get("header_detect_col", "型号")
    max_scan = reader.get("max_scan_rows", 12)
    skip = set(reader.get("skip_sheets", []))
    xl = pd.ExcelFile(path)
    out = []
    for name in xl.sheet_names:
        if name in skip:
            continue
        hr = find_header_row(path, name, detect_col, max_scan)
        if hr is None:
            continue
        df = pd.read_excel(path, sheet_name=name, header=hr, dtype=str)
        if detect_col not in [str(c) for c in df.columns]:
            continue
        out.append((name, df))
    return out


# ============ mapper：按配置做列映射 ============

def map_row(row: dict, cfg: dict, sheet: str, row_no: int) -> dict:
    mapping = cfg.get("mapping", {})
    spec_cols = cfg.get("spec_cols", [])
    mapped = {"_sheet": sheet, "_row": row_no}
    for src_col, val in row.items():
        src = str(src_col)
        target = mapping.get(src, src)
        if target.startswith("_"):
            continue
        mapped[target] = val
    # spec JSONB
    spec = {}
    for c in spec_cols:
        v = str(row.get(c, "") or "").strip()
        if v and v.lower() != "nan":
            spec[c] = v
    mapped["spec"] = spec
    return mapped


# ============ validator：必填/枚举校验 ============

def clean(v):
    s = str(v or "").strip()
    return "" if s.lower() == "nan" else s


def validate_row(m: dict, cfg: dict) -> list[str]:
    errs = []
    for f in cfg.get("required", []):
        if not clean(m.get(f)):
            errs.append(f"缺必填字段 {f}")
    return errs


def is_empty(m: dict) -> bool:
    return not any(clean(v) for k, v in m.items() if not k.startswith("_"))


def _json_safe(d: dict) -> str:
    """NaN/Infinity 等非 JSON 值转成 null，保证 raw_data 可入库。"""
    import math

    def fix(o):
        if isinstance(o, float) and (math.isnan(o) or math.isinf(o)):
            return None
        if isinstance(o, dict):
            return {k: fix(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [fix(v) for v in o]
        return o

    return json.dumps(fix(d), ensure_ascii=False)


# ============ loader：raw_data 留档 + 入库 ============

def load(rows: list[dict], dsn: str, cfg: dict, source_file: str,
         source_kind: str = "facts") -> dict:
    """rows: [mapped dict]，每条含 _sheet/_row。"""
    sha = hashlib.sha256(Path(source_file).read_bytes()).hexdigest()
    stats = {"loaded": 0, "pending": 0, "bad": 0, "skip": 0}
    bad_sample = []

    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            # 1) 建批次
            cur.execute("""
                INSERT INTO import_batch
                  (source_file, file_sha256, file_type, source_kind, status, started_by)
                VALUES (%s,%s,'xlsx',%s,'running',%s)
                RETURNING id
            """, (os.path.basename(source_file), sha, source_kind,
                  os.environ.get("USER", "cli")))
            batch_id = cur.fetchone()[0]

            # 2) 逐行：留档 raw_data，同时入库
            seen = set()
            for m in rows:
                cur.execute("""
                    INSERT INTO raw_data (batch_id, sheet_name, row_no, raw, mapped, verdict)
                    VALUES (%s,%s,%s,%s,%s,'pending')
                """, (batch_id, m.get("_sheet"), m.get("_row"),
                      _json_safe({k: v for k, v in m.items()
                                  if not k.startswith("_") and k != "spec"}),
                      _json_safe(m)))

                if is_empty(m):
                    stats["skip"] += 1
                    cur.execute("""UPDATE raw_data SET verdict='bad',
                                   errs=array_append(errs,'空行')
                                   WHERE id=(SELECT max(id) FROM raw_data WHERE batch_id=%s)""",
                                (batch_id,))
                    continue

                errs = validate_row(m, cfg)
                sku = clean(m.get("sku_code"))
                if errs:
                    stats["pending"] += 1
                    cur.execute("""UPDATE raw_data SET verdict='pending_manual', errs=%s
                                   WHERE id=(SELECT max(id) FROM raw_data WHERE batch_id=%s)""",
                                (errs, batch_id))
                    if len(bad_sample) < 8:
                        bad_sample.append({"sheet": m.get("_sheet"), "errs": errs,
                                           "sku": sku})
                    continue
                if sku in seen:
                    stats["bad"] += 1
                    cur.execute("""UPDATE raw_data SET verdict='bad',
                                   errs=array_append(errs,'重复型号')
                                   WHERE id=(SELECT max(id) FROM raw_data WHERE batch_id=%s)""",
                                (batch_id,))
                    continue
                seen.add(sku)

                spec = m.get("spec", {})
                cur.execute("""
                    INSERT INTO sku (sku_code, category, name_zh, name_en, spec, status)
                    VALUES (%s,%s,%s,%s,%s,'draft')
                    ON CONFLICT (sku_code) DO UPDATE
                    SET name_zh=EXCLUDED.name_zh, name_en=EXCLUDED.name_en,
                        category=EXCLUDED.category, spec=EXCLUDED.spec
                """, (sku, m.get("category"), m.get("name_zh"), m.get("name_en"),
                      _json_safe(spec)))
                stats["loaded"] += 1
                cur.execute("""UPDATE raw_data SET verdict='loaded'
                               WHERE id=(SELECT max(id) FROM raw_data WHERE batch_id=%s)""",
                            (batch_id,))

            # 3) 批次收尾
            cur.execute("""
                UPDATE import_batch SET total_rows=%s, loaded_rows=%s,
                  pending_rows=%s, bad_rows=%s, status='done', finished_at=now()
                WHERE id=%s
            """, (len(rows), stats["loaded"], stats["pending"],
                  stats["bad"], batch_id))
        conn.commit()

    return {"batch_id": batch_id, **stats, "bad_sample": bad_sample,
            "file_sha256": sha[:16]}


# ============ CLI ============

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--config", default="jiacong_facts")
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--kind", default="facts",
                    choices=["facts", "faq", "manual", "image_meta"])
    a = ap.parse_args()

    cfg = get_mapping(a.config, a.dsn)
    print(f"[ingest_facts] 配置源: {cfg.get('_source')} ({a.config})")

    sheets = read_sheets(a.path, cfg)
    rows = []
    for sheet, df in sheets:
        for idx, r in df.iterrows():
            m = map_row(r.to_dict(), cfg, sheet, idx)
            rows.append(m)
    print(f"[ingest_facts] 读取 {len(rows)} 行 / {len(sheets)} sheet")

    result = load(rows, a.dsn, cfg, a.path, a.kind)
    print(json.dumps(result, ensure_ascii=False, indent=2))
