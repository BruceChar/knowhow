"""配置加载模块：映射配置支持 YAML 文件与数据库双通道。

优先级：YAML 文件 > 数据库（DB 是运行时可改的管理通道，YAML 是部署基线）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent.parent / "config" / "mappings"


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
                "SELECT config FROM mapping_config WHERE config_key=%s AND active",
                (config_key,))
            row = cur.fetchone()
    if not row:
        raise KeyError(f"数据库无映射配置: {config_key}")
    val = row[0]
    # 兼容 jsonb（psycopg3 自动反序列化为 dict）与 JSON 文本两种存储
    if isinstance(val, dict):
        return val
    return json.loads(val)


def get_mapping(config_key: str, dsn: str | None = None) -> dict[str, Any]:
    """加载映射配置：YAML 优先，数据库兜底。"""
    try:
        cfg = load_mapping_from_yaml(config_key)
        cfg["_source"] = "yaml"
        return cfg
    except FileNotFoundError:
        if dsn:
            cfg = load_mapping_from_db(config_key, dsn)
            cfg["_source"] = "db"
            return cfg
        raise
