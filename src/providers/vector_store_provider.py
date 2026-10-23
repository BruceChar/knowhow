"""Vector Store Provider 防腐层：pgvector（PostgreSQL 内置向量检索）。

上层通过 get_vector_store("pgvector") 拿 provider：
  vs.collection = "faq"        # 切换集合（表）
  vs.search(embedding, top_k, filters) -> [Hit(id, score, payload)]
filters 支持 {"status": "approved"} 等精确过滤（数据级权限）。
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

import psycopg
from pgvector.psycopg import register_vector


@dataclass
class Hit:
    id: Any
    score: float
    payload: dict = field(default_factory=dict)


# ---------------- 抽象接口 ----------------
class VectorStoreProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def search(self, embedding: list[float], top_k: int = 5,
               filters: dict | None = None) -> list[Hit]: ...


# ---------------- pgvector ----------------
class PgVectorStoreProvider(VectorStoreProvider):
    name = "pgvector"

    def __init__(self, dsn: str | None = None):
        self.dsn = dsn or os.environ.get(
            "DATABASE_URL", "postgresql://rag:rag_dev_2026@localhost:5433/rag")
        self.collection = "faq"  # 默认集合；上层可覆盖（如 "knowledge_card"）

    def _conn(self):
        conn = psycopg.connect(self.dsn)
        register_vector(conn)
        return conn

    def search(self, embedding: list[float], top_k: int = 5,
               filters: dict | None = None) -> list[Hit]:
        # pgvector 0.5.x + psycopg3 的 register_vector 适配器失效（list 被序列化成 {}），
        # 用文本直传绕开：向量以 "[a,b,c]" 字符串传给 ::vector
        vec_text = "[" + ",".join(f"{x:.8f}" for x in embedding) + "]"
        conds, params = [], []
        if filters:
            for k, v in filters.items():
                conds.append(f"{k} = %s")
                params.append(v)
        where = (" WHERE " + " AND ".join(conds)) if conds else ""
        table = self.collection
        sql = (f"SELECT id, 1 - (embedding <=> %s::vector) AS score "
               f"FROM {table}{where} "
               f"ORDER BY embedding <=> %s::vector LIMIT %s")
        params = [vec_text] + params + [vec_text, top_k]
        with self._conn() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        return [Hit(id=r[0], score=float(r[1])) for r in rows]


# ---------------- 注册表与工厂 ----------------
PROVIDER_REGISTRY: dict[str, type[VectorStoreProvider]] = {
    "pgvector": PgVectorStoreProvider,
}


def get_vector_store(backend: str | None = None) -> VectorStoreProvider:
    """防腐层工厂：VECTOR_BACKEND 环境变量 > 显式参数 > 默认 pgvector。"""
    name = backend or os.environ.get("VECTOR_BACKEND", "pgvector")
    cls = PROVIDER_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"未知 vector store: {name!r}，可选 {list(PROVIDER_REGISTRY)}")
    return cls()
