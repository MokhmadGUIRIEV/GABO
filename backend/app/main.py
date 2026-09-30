from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import FileResponse

from .db import init_db
from .routes.admin import router as admin_router
from .routes.auth import router as auth_router
from .routes.rooms import router as rooms_router
from .ws import router as ws_router

IS_PRODUCTION = os.environ.get("GABO_ENV") == "production"
SECRET_KEY = os.environ.get("GABO_SECRET_KEY")
if not SECRET_KEY:
    if IS_PRODUCTION:
        # A random key would log everyone out on every restart/wake-up.
        raise RuntimeError("GABO_SECRET_KEY doit être défini en production.")
    SECRET_KEY = secrets.token_hex(32)

app = FastAPI(title="GABO")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax", https_only=IS_PRODUCTION)

app.include_router(auth_router)
app.include_router(admin_router)
app.include_router(rooms_router)
app.include_router(ws_router)

FRONTEND_DIR = (Path(__file__).resolve().parents[2] / "frontend").resolve()


@app.get("/api/health")
def health() -> dict:
    return {"ok": True}


@app.on_event("startup")
def on_startup() -> None:
    init_db()


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")

    @app.get("/{page:path}")
    def serve_frontend(page: str = ""):
        # Resolve then check containment: the path comes from the URL and may
        # contain encoded "../" trying to escape the frontend directory.
        candidate = (FRONTEND_DIR / (page or "index.html")).resolve()
        if candidate.is_file() and candidate.is_relative_to(FRONTEND_DIR):
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIR / "index.html")
