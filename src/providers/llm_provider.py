"""LLM Provider 防腐层：DeepSeek / OpenAI 兼容 API 可切换。

generate.py 当前直接使用 OpenAI client；本层为统一模型接入预留——
未来接 Moonshot/通义/本地 ollama 时只需新增 Provider，上层 draft_reply 不变。
"""
from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Any

# ---------------- 抽象接口 ----------------
class LLMProvider(ABC):
    name: str = "abstract"

    @abstractmethod
    def chat(self, messages: list[dict], temperature: float = 0.2,
             max_tokens: int | None = None) -> str:
        """messages（OpenAI 格式）→ 模型回复文本。"""


# ---------------- OpenAI 兼容（DeepSeek 等） ----------------
class OpenAICompatibleProvider(LLMProvider):
    name = "openai"

    def __init__(self, base_url: str | None = None, api_key: str | None = None,
                 model: str | None = None):
        self.base_url = base_url or os.environ.get(
            "LLM_BASE_URL", "https://api.deepseek.com/v1")
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self.model = model or os.environ.get("LLM_MODEL", "deepseek-chat")
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key)
        return self._client

    def chat(self, messages: list[dict], temperature: float = 0.2,
             max_tokens: int | None = None) -> str:
        client = self._get_client()
        kwargs = {"model": self.model, "temperature": temperature,
                  "messages": messages}
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        resp = client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content


# ---------------- 本地 ollama（可选） ----------------
class OllamaProvider(LLMProvider):
    name = "ollama"

    def __init__(self, base_url: str | None = None, model: str | None = None):
        self.base_url = base_url or os.environ.get("OLLAMA_BASE_URL",
                                                   "http://localhost:11434")
        self.model = model or os.environ.get("OLLAMA_MODEL", "qwen2.5:7b")
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(base_url=f"{self.base_url}/v1", api_key="ollama")
        return self._client

    def chat(self, messages: list[dict], temperature: float = 0.2,
             max_tokens: int | None = None) -> str:
        client = self._get_client()
        kwargs = {"model": self.model, "temperature": temperature,
                  "messages": messages}
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        resp = client.chat.completions.create(**kwargs)
        return resp.choices[0].message.content


# ---------------- 注册表与工厂 ----------------
PROVIDER_REGISTRY: dict[str, type[LLMProvider]] = {
    "openai": OpenAICompatibleProvider,
    "ollama": OllamaProvider,
}


def get_llm_provider(backend: str | None = None) -> LLMProvider:
    """防腐层工厂：LLM_BACKEND 环境变量 > 显式参数 > 默认 openai 兼容。"""
    name = backend or os.environ.get("LLM_BACKEND", "openai")
    cls = PROVIDER_REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"未知 LLM backend: {name!r}，可选 {list(PROVIDER_REGISTRY)}")
    return cls()
