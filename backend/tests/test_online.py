import os
import sys
import tempfile
from pathlib import Path

os.environ["GABO_DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test_online.db")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.game.engine import GameError
from app.main import app
from app.rooms import RoomSession, room_manager

_counter = 0


def new_user(client: TestClient, name: str) -> None:
    global _counter
    _counter += 1
    r = client.post("/api/auth/register", json={
        "email": f"{name.lower()}{_counter}@example.com",
        "display_name": name,
        "password": "secret123",
    })
    assert r.status_code == 200, r.text


def call(ws, action: str, **payload) -> dict:
    """Send an action and return the reply to *that* request, skipping broadcasts."""
    global _counter
    _counter += 1
    req_id = f"r{_counter}"
    ws.send_json({"action": action, "id": req_id, **payload})
    while True:
        msg = ws.receive_json()
        if msg.get("id") == req_id:
            return msg


@pytest.fixture()
def clients():
    with TestClient(app) as host, TestClient(app) as friend, TestClient(app) as stranger:
        new_user(host, "Hote")
        new_user(friend, "Ami")
        new_user(stranger, "Inconnu")
        yield host, friend, stranger


def create_online_room(host, friend) -> str:
    room = host.post("/api/rooms", json={"mode": "online"}).json()
    assert room["mode"] == "online"
    assert room["you"] == "p0"
    assert room["started"] is False
    joined = friend.post(f"/api/rooms/{room['code']}/join").json()
    assert joined["you"] == "p1"
    assert joined["player_names"] == {"p0": "Hote", "p1": "Ami"}
    return room["code"]


def test_join_is_idempotent_and_visible_to_everyone(clients):
    host, friend, _ = clients
    code = create_online_room(host, friend)
    again = friend.post(f"/api/rooms/{code}/join").json()
    assert again["you"] == "p1"
    assert host.get(f"/api/rooms/{code}").json()["player_ids"] == ["p0", "p1"]


def test_user_who_did_not_join_cannot_connect(clients):
    host, friend, stranger = clients
    code = create_online_room(host, friend)
    with pytest.raises(WebSocketDisconnect) as refused:
        with stranger.websocket_connect(f"/ws/rooms/{code}") as ws:
            ws.receive_json()
    assert refused.value.code == 4403


def test_unknown_room_is_reported_as_gone(clients):
    host, _, _ = clients
    with pytest.raises(WebSocketDisconnect) as refused:
        with host.websocket_connect("/ws/rooms/NOPE0") as ws:
            ws.receive_json()
    assert refused.value.code == 4404


def test_only_host_can_start_and_no_join_after_start(clients):
    host, friend, stranger = clients
    code = create_online_room(host, friend)
    with host.websocket_connect(f"/ws/rooms/{code}") as ws_host, \
            friend.websocket_connect(f"/ws/rooms/{code}") as ws_friend:
        assert ws_host.receive_json()["you"] == "p0"
        assert ws_friend.receive_json()["you"] == "p1"

        refused = call(ws_friend, "start_round")
        assert refused["type"] == "error"

        started = call(ws_host, "start_round")
        assert started["state"]["phase"] == "initial_peek"

    late = stranger.post(f"/api/rooms/{code}/join")
    assert late.status_code == 409


def test_client_cannot_act_or_look_as_another_player(clients):
    host, friend, _ = clients
    code = create_online_room(host, friend)
    with host.websocket_connect(f"/ws/rooms/{code}") as ws_host, \
            friend.websocket_connect(f"/ws/rooms/{code}") as ws_friend:
        ws_host.receive_json()
        ws_friend.receive_json()
        call(ws_host, "start_round")

        # The friend pretends to be the host: the server must ignore that
        # and register the peek for the friend's own seat.
        spoofed = call(ws_friend, "peek_initial", player_id="p0", indices=[0, 1])
        assert spoofed["type"] == "state"
        assert spoofed["state"]["players_peeked"] == ["p1"]
        assert spoofed["viewer_id"] == "p1"

        # The host peeks too; the friend asking for the host's view must
        # still only get their own view (host's cards stay hidden).
        call(ws_host, "peek_initial", indices=[0, 1])
        friend_view = call(ws_friend, "get_state", viewer_id="p0")["state"]
        host_seat = next(p for p in friend_view["players"] if p["id"] == "p0")
        assert all(slot["hidden"] for slot in host_seat["hand"])

        # Both peeked -> the round is running. Whoever is NOT the current
        # player tries to draw while spoofing the current player's id.
        assert friend_view["phase"] == "turn"
        current = friend_view["current_player_id"]
        waiting_ws = ws_friend if current == "p0" else ws_host
        spoofed_draw = call(waiting_ws, "draw_card", player_id=current)
        assert spoofed_draw["type"] == "error"


def test_claim_seat_rejects_seventh_player():
    room = RoomSession(code="X", owner_user_id=1, player_ids=[], player_names={}, engine=None, mode="online")
    for uid in range(1, 7):
        room.claim_seat(uid, f"J{uid}")
    with pytest.raises(GameError):
        room.claim_seat(7, "J7")


def test_start_needs_two_players():
    room = room_manager.create_online_room(owner_user_id=999, owner_name="Seul")
    with pytest.raises(GameError):
        room.start_engine()


def test_local_rooms_still_work_as_before(clients):
    host, _, _ = clients
    room = host.post("/api/rooms", json={"player_names": ["A", "B"]}).json()
    assert room["mode"] == "local"
    with host.websocket_connect(f"/ws/rooms/{room['code']}") as ws:
        ws.receive_json()
        call(ws, "start_round")
        # Local mode trusts the (single) controlling browser's player_id.
        reply = call(ws, "peek_initial", player_id="p1", indices=[0, 1])
        assert reply["state"]["players_peeked"] == ["p1"]
