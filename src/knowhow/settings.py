"""Environment-backed application settings."""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)
except ImportError:
    pass


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(int(default))).strip().lower() in {
        "1", "true", "yes", "on",
    }


@dataclass(frozen=True)
class Settings:
    database_url: str = os.environ.get(
        "KNOWHOW_DATABASE_URL",
        "postgresql://knowhow:knowhow_dev@localhost:5433/knowhow",
    )
    secret: str = os.environ.get("KNOWHOW_SECRET") or secrets.token_urlsafe(32)
    cookie_secure: bool = _bool("KNOWHOW_COOKIE_SECURE")
    dev_auth: bool = _bool("KNOWHOW_DEV_AUTH")
    session_hours: int = int(os.environ.get("KNOWHOW_SESSION_HOURS", "24"))
    llm_base_url: str = os.environ.get("KNOWHOW_LLM_BASE_URL", "")
    llm_api_key: str = os.environ.get("KNOWHOW_LLM_API_KEY", "")
    llm_model: str = os.environ.get("KNOWHOW_LLM_MODEL", "")


settings = Settings()

