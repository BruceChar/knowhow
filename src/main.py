"""Canonical ASGI entrypoint.

The product has one FastAPI app. The old tutorial-only `/v1/inquiry` module
is kept as an alias so existing launch commands fail closed with authentication
instead of exposing an unauthenticated parallel API.
"""
from src.api.admin_api import app

__all__ = ["app"]
