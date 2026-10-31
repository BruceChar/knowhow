"""Canonical ASGI entrypoint.

The product has one FastAPI app. The old tutorial-only `/v1/inquiry` module
is kept as an alias so existing launch commands fail closed with authentication
instead of exposing an unauthenticated parallel API.
"""
import os

from src.api.admin_api import app

__all__ = ["app"]


def run() -> None:
    """命令行启动：uv run knowhow（等价于 uv run app/index）。"""
    import uvicorn

    host = os.environ.get("APP_HOST", "0.0.0.0")
    port = int(os.environ.get("APP_PORT", "8000"))
    uvicorn.run("src.main:app", host=host, port=port)
