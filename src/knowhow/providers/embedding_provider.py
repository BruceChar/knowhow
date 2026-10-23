"""Embedding Provider 防腐层：local bge-m3 / local qwen3 / API 可切换。

上层（检索/评测/入录）永远通过 get_embedding_provider() 拿 provider，
调用 embed()（文档批量）或 embed_query()（查询单条），不感知具体后端。

后端选择（优先级）：
  1. EMBEDDING_BACKEND 环境变量（local / qwen3 / api）
  2. get_embedding_provider(backend) 显式参数
  3. 默认 local（bge-m3）

模型缓存定位：HF_HUB_CACHE 环境变量 > 项目 data/models（离线部署）> 系统缓存。
bge-m3 项目缓存缺失时自动回退系统缓存（避免强制离线路径失败）。
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

# ---------------- 抽象接口 ----------------
class EmbeddingProvider(ABC):
    name: str = "abstract"
    dim: int = 1024

    @abstractmethod
    def embed(self, texts: list[str], batch_size: int = 64) -> list[list[float]]:
        """文档批量 → 向量（已归一化）。"""

    def embed_query(self, text: str) -> list[float]:
        """查询编码：默认与文档同编码；Qwen3 等 instruction 模型覆写加前缀。
        上层检索永远调 embed_query，不感知模型差异。"""
        return self.embed([text])[0]


def _cache_dir() -> str | None:
    """模型缓存目录：HF_HUB_CACHE 环境变量 > 项目 data/models（离线部署）> None（系统缓存）。

    data/models 存在时自动使用——模型随项目走，任何机器 clone 项目即可加载。"""
    env = os.environ.get("HF_HUB_CACHE")
    if env:
        return env
    proj = Path(__file__).resolve().parent.parent.parent.parent / "data" / "models"
    if proj.is_dir():
        return str(proj)
    return None


# ---------------- 本地 bge-m3（sentence-transformers） ----------------
class LocalBgeM3Provider(EmbeddingProvider):
    name = "local"

    def __init__(self, model_name: str | None = None, device: str | None = None,
                 cache_dir: str | None = None):
        self._model = None
        self.model_name = model_name or os.environ.get("EMBEDDING_MODEL",
                                                       "BAAI/bge-m3")
        self.device = device or ("mps" if os.environ.get("PYTORCH_ENABLE_MPS") else "cpu")
        self.cache_dir = cache_dir or _cache_dir()

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer
            kwargs = {"device": self.device}
            if self.cache_dir and self._model_in_cache():
                kwargs["cache_folder"] = self.cache_dir
            # 项目缓存没有该模型时，用 sentence-transformers 系统缓存（回退）
            self._model = SentenceTransformer(self.model_name, **kwargs)
        return self._model

    def _model_in_cache(self) -> bool:
        """项目缓存目录里是否存在该模型（models--org--name 结构）。"""
        if not self.cache_dir:
            return False
        safe = "models--" + self.model_name.replace("/", "--")
        return (Path(self.cache_dir) / safe).is_dir()

    def embed(self, texts, batch_size=64):
        enc = self._load()
        out = []
        for i in range(0, len(texts), batch_size):
            chunk = texts[i:i + batch_size]
            vecs = enc.encode(chunk, normalize_embeddings=True)
            out.extend(v.tolist() for v in vecs)
        return out

    @property
    def dim(self) -> int:
        return 1024  # bge-m3


# ---------------- 本地 Qwen3-Embedding（transformers） ----------------
class Qwen3LocalProvider(EmbeddingProvider):
    name = "qwen3"
    INSTRUCTION = ("Given a customer inquiry, retrieve the matching FAQ answer "
                   "that best addresses the customer's need.")

    def __init__(self, model_name: str | None = None, device: str | None = None,
                 cache_dir: str | None = None):
        self._model = None
        self._tokenizer = None
        self.model_name = model_name or os.environ.get(
            "EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
        self.device = device or ("mps" if os.environ.get("PYTORCH_ENABLE_MPS") else "cpu")
        self.cache_dir = cache_dir or _cache_dir()

    def _load(self):
        if self._model is None:
            from transformers import AutoModel, AutoTokenizer
            kwargs = {"trust_remote_code": True}
            if self.cache_dir:
                kwargs["cache_dir"] = self.cache_dir
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name, **kwargs)
            self._model = AutoModel.from_pretrained(
                self.model_name, **kwargs).to(self.device)
            self._model.eval()
        return self._model, self._tokenizer

    def embed(self, texts, batch_size=16):
        import torch
        model, tok = self._load()
        out = []
        for i in range(0, len(texts), batch_size):
            chunk = texts[i:i + batch_size]
            inputs = tok(chunk, padding=True, truncation=True,
                         max_length=512, return_tensors="pt").to(self.device)
            with torch.no_grad():
                out_vecs = model(**inputs).squeeze(0).detach().cpu().numpy()
            for v in out_vecs:
                v = v / (v ** 2).sum() ** 0.5  # 归一化
                out.append(v.tolist())
        return out

    def embed_query(self, text: str) -> list[float]:
        # Qwen3 检索需 instruction 前缀（query 与文档格式区分）
        return self.embed([self.INSTRUCTION + "\n" + text])[0]

    @property
    def dim(self) -> int:
        return 1024  # Qwen3-Embedding-0.6B（MRL，可截断维度）


# ---------------- API（占位：未来接 Qwen3-Embedding API / OpenAI embeddings） ----------------
class ApiEmbeddingProvider(EmbeddingProvider):
    name = "api"

    def __init__(self, api_key: str | None = None, model: str | None = None,
                 base_url: str | None = None):
        self.api_key = api_key or os.environ.get("EMBEDDING_API_KEY", "")
        self.model = model or os.environ.get("EMBEDDING_API_MODEL",
                                             "text-embedding-3-small")
        self.base_url = base_url or os.environ.get("EMBEDDING_API_BASE")
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            kwargs = {"api_key": self.api_key}
            if self.base_url:
                kwargs["base_url"] = self.base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def embed(self, texts, batch_size=100):
        client = self._get_client()
        out = []
        for i in range(0, len(texts), batch_size):
            chunk = texts[i:i + batch_size]
            resp = client.embeddings.create(model=self.model, input=chunk)
            out.extend([d.embedding for d in resp.data])
        return out

    @property
    def dim(self) -> int:
        return int(os.environ.get("EMBEDDING_API_DIM", 1024))


# ---------------- 注册表与工厂 ----------------
PROVIDER_REGISTRY: dict[str, type[EmbeddingProvider]] = {
    "local": LocalBgeM3Provider,
    "qwen3": Qwen3LocalProvider,
    "api": ApiEmbeddingProvider,
}


def get_embedding_provider(backend: str | None = None) -> EmbeddingProvider:
    """防腐层工厂：EMBEDDING_BACKEND 环境变量 > 显式参数 > 默认 local。"""
    name = backend or os.environ.get("EMBEDDING_BACKEND", "local")
    cls = PROVIDER_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"未知 embedding backend: {name!r}，可选 {list(PROVIDER_REGISTRY)}")
    return cls()
