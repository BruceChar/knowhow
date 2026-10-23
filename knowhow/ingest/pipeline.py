"""入录编排器：统一入口，按文件类型路由到对应 ETL。

工作流：上传/选文件 → 解析（reader）→ 映射（config）→ 校验 → 打标 → 审核(draft)
后端状态流转：raw_data(pending→loaded/pending_manual/bad) + import_batch(done/failed)

用法：
  uv run python app/ingest_pipeline.py <文件路径> --kind facts|faq --config jiacong_facts
  uv run python app/ingest_pipeline.py data/raw/faq.json --kind faq
"""
import argparse, json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


def run(path: str, kind: str, config: str | None = None,
        dsn: str | None = None, backend: str | None = None) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {path}")

    kind = kind or ("faq" if p.suffix.lower() == ".json" else "facts")
    if kind == "faq":
        from knowhow.ingest.faq import load_faq
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        return load_faq(data, dsn or "postgresql://rag:rag_dev_2026@localhost:5433/rag",
                        str(p), backend)
    if kind == "facts":
        from knowhow.ingest.facts import load, read_sheets
        from knowhow.config_loader import get_mapping
        dsn = dsn or "postgresql://rag:rag_dev_2026@localhost:5433/rag"
        cfg = get_mapping(config or "jiacong_facts", dsn)
        rows = []
        for sheet, df in read_sheets(str(p), cfg):
            from knowhow.ingest.facts import map_row
            for idx, r in df.iterrows():
                rows.append(map_row(r.to_dict(), cfg, sheet, idx))
        return load(rows, dsn, cfg, str(p), "facts")
    raise ValueError(f"不支持的来源类型: {kind}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--kind", choices=["facts", "faq"])
    ap.add_argument("--config", default=None)
    ap.add_argument("--backend", default=None)
    a = ap.parse_args()
    print(json.dumps(run(a.path, a.kind, a.config,
                         backend=a.backend), ensure_ascii=False, indent=2))
