"""配置加载模块：数据库中的已发布模板优先，YAML 仅作为种子/离线兜底。"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config" / "mappings"


def load_mapping_from_yaml(config_key: str) -> dict[str, Any]:
    """从 config/mappings/{config_key}.yaml 读取映射配置。"""
    p = CONFIG_DIR / f"{config_key}.yaml"
    if not p.exists():
        raise FileNotFoundError(f"映射配置不存在: {p}")
    with open(p, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_mapping_from_db(config_key: str, dsn: str) -> dict[str, Any]:
    """从 mapping_config 表读取（运行时管理通道）。"""
    import psycopg
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, config, version FROM mapping_config "
                "WHERE config_key=%s AND active",
                (config_key,))
            row = cur.fetchone()
    if not row:
        raise KeyError(f"数据库无映射配置: {config_key}")
    val = row[1]
    # 兼容 jsonb（psycopg3 自动反序列化为 dict）与 JSON 文本两种存储
    cfg = val if isinstance(val, dict) else json.loads(val)
    cfg["_mapping_id"] = row[0]
    cfg["_template_version"] = row[2]
    return cfg


def get_mapping(config_key: str, dsn: str | None = None) -> dict[str, Any]:
    """加载映射配置：DB 已发布版本优先，YAML 作为种子兜底。"""
    if dsn:
        try:
            cfg = load_mapping_from_db(config_key, dsn)
            cfg["_source"] = "db"
            return cfg
        except KeyError:
            # Missing DB configuration falls back to the checked-in baseline.
            pass
    try:
        cfg = load_mapping_from_yaml(config_key)
        cfg["_source"] = "yaml"
        cfg["_template_version"] = 1
        return cfg
    except FileNotFoundError:
        raise
