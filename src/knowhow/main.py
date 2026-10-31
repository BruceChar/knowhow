"""Knowhow V2 FastAPI application."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlparse

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from knowhow.api.deps import SESSION_COOKIE
from knowhow.api.routes import (
    assistant, auth, dashboard, imports, knowledge, team, training,
)
from knowhow.settings import settings

WEB_DIR = Path(__file__).resolve().parent / "web"

app = FastAPI(title="Knowhow", version="0.2.0")
app.mount("/assets", StaticFiles(directory=str(WEB_DIR / "assets")), name="assets")


@app.middleware("http")
async def csrf_guard(request: Request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        has_session = bool(request.cookies.get(SESSION_COOKIE))
        if has_session and request.url.path not in {
            "/api/auth/login", "/api/setup",
        }:
            if request.headers.get("x-knowhow-csrf") != "1":
                return JSONResponse({"detail": "缺少 CSRF 校验头"}, status_code=403)
            origin = request.headers.get("origin", "")
            if origin and urlparse(origin).netloc != request.url.netloc:
                return JSONResponse({"detail": "拒绝跨站请求"}, status_code=403)
    return await call_next(request)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(str(WEB_DIR / "index.html"))


@app.get("/health")
def health():
    database = "ok"
    try:
        with psycopg.connect(settings.database_url, connect_timeout=3) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
    except Exception as exc:
        database = str(exc)
    return {
        "status": "ok" if database == "ok" else "degraded",
        "database": database,
        "version": "0.2.0",
    }


for module in (auth, dashboard, knowledge, imports, assistant, team, training):
    app.include_router(module.router)


def run() -> None:
    import uvicorn

    uvicorn.run(
        "knowhow.main:app",
        host=os.environ.get("KNOWHOW_HOST", "0.0.0.0"),
        port=int(os.environ.get("KNOWHOW_PORT", "8000")),
    )


if __name__ == "__main__":
    run()

