from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import FileResponse

from .db import init_db
from .routes.auth import router as auth_router
from .routes.rooms import router as rooms_router
from .ws import router as ws_router

SECRET_KEY = os.environ.get("GABO_SECRET_KEY") or secrets.token_hex(32)

app = FastAPI(title="GABO")
app.add_middleware(SessionMiddleware, secret_key=SECRET_KEY, same_site="lax")

app.include_router(auth_router)
app.include_router(rooms_router)
app.include_router(ws_router)

FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"


@app.on_event("startup")
def on_startup() -> None:
    init_db()


if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR / "static"), name="static")

    @app.get("/{page:path}")
    def serve_frontend(page: str = ""):
        candidate = FRONTEND_DIR / (page or "index.html")
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(FRONTEND_DIR / "index.html")
