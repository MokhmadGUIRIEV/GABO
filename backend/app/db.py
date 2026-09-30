from __future__ import annotations

import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if url:
        # Hosted Postgres (e.g. Neon) hands out postgres:// or postgresql://
        # URLs; point SQLAlchemy at the psycopg (v3) driver we install.
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url
    db_path = os.environ.get("GABO_DB_PATH", os.path.join(os.path.dirname(__file__), "..", "gabo.db"))
    return f"sqlite:///{db_path}"


DATABASE_URL = _database_url()

if DATABASE_URL.startswith("sqlite"):
    engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
else:
    # pool_pre_ping: free hosted databases drop idle connections.
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from . import models  # noqa: F401  (ensure models are registered on Base)

    Base.metadata.create_all(bind=engine)
    _add_missing_columns()


def _add_missing_columns() -> None:
    # create_all() never alters existing tables: databases created before a
    # column was added need it added by hand, or every query would fail.
    columns = {c["name"] for c in inspect(engine).get_columns("player_results")}
    if "user_id" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE player_results ADD COLUMN user_id INTEGER REFERENCES users(id)"))
