import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("GABO_DB_PATH", os.path.join(tempfile.mkdtemp(), "test_app.db"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.mark.parametrize("path", [
    "/..%2fbackend%2fapp%2fmain.py",
    "/%2e%2e/backend/requirements.txt",
    "/..%2f..%2fetc%2fpasswd",
    "/%2e%2e%2f%2e%2e%2fbackend%2fapp%2fdb.py",
])
def test_frontend_route_cannot_read_files_outside_frontend(client, path):
    r = client.get(path)
    # Falls back to the home page instead of leaking the file.
    assert r.status_code == 200
    assert "<title>GABO</title>" in r.text
    assert "SECRET_KEY" not in r.text and "fastapi" not in r.text and "root:" not in r.text


def test_frontend_pages_and_static_files_still_served(client):
    assert "<title>GABO — Jouer</title>" in client.get("/lobby.html").text
    for page, title in [
        ("account", "Mon compte"),
        ("leaderboard", "Classement"),
        ("history", "Historique"),
        ("admin", "Administration"),
    ]:
        assert f"<title>GABO — {title}</title>" in client.get(f"/{page}.html").text
    assert client.get("/static/js/game.js").status_code == 200
    assert client.get("/static/js/nav.js").status_code == 200


def test_health_endpoint(client):
    assert client.get("/api/health").json() == {"ok": True}
