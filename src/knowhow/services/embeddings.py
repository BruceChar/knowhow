"""Lazy local embedding provider used by pgvector retrieval."""
from __future__ import annotations

import os
import threading

MODEL_NAME = os.environ.get("KNOWHOW_EMBEDDING_MODEL", "BAAI/bge-m3")
_model = None
_lock = threading.Lock()


class EmbeddingUnavailable(RuntimeError):
    pass


def get_model():
    global _model
    if _model is not None:
        return _model
    with _lock:
        if _model is not None:
            return _model
        try:
            os.environ.setdefault("HF_HUB_OFFLINE", "1")
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(MODEL_NAME)
        except Exception as exc:
            raise EmbeddingUnavailable(str(exc)) from exc
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    model = get_model()
    vectors = model.encode(
        texts,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return [list(map(float, vector)) for vector in vectors]


def embed_text(text: str) -> list[float]:
    return embed_texts([text])[0]


def pgvector_literal(vector: list[float]) -> str:
    return "[" + ",".join(f"{value:.8f}" for value in vector) + "]"


def model_name() -> str:
    return MODEL_NAME

