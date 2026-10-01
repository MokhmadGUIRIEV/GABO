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


@pytest.fixture(autouse=True)
def no_doubles_window(monkeypatch):
    # These tests chain moves instantly; the 3s doubles window is covered by
    # the engine tests.
    monkeypatch.setattr("app.game.engine.DOUBLES_WINDOW_SECONDS", 0)


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


def finish_online_game(host, friend, winner: str) -> str:
    """Play a 2-player online game to game over; `winner` is "host" or "friend"."""
    from app.game.cards import Card, Rank, Suit

    code = create_online_room(host, friend)
    with host.websocket_connect(f"/ws/rooms/{code}") as ws_host, \
            friend.websocket_connect(f"/ws/rooms/{code}") as ws_friend:
        ws_host.receive_json()
        ws_friend.receive_json()
        call(ws_host, "start_round")
        call(ws_host, "peek_initial", indices=[0, 1])
        call(ws_friend, "peek_initial", indices=[0, 1])

        # Rig the table: whoever's turn it is calls GABO with a losing hand
        # while already at 70 points, so they get eliminated (+35).
        engine = room_manager.get(code).engine
        caller = engine.current_player
        other = next(p for p in engine.players if p.id != caller.id)
        caller.score = 70
        caller.hand = [Card(Rank.KING, Suit.HEART)]
        other.hand = [Card(Rank.ACE, Suit.HEART)]
        loser_seat = caller.id
        winner_seat = "p0" if winner == "host" else "p1"
        if loser_seat == winner_seat:
            # Swap roles so the requested player wins.
            caller.score, other.score = 0, 0
            caller.hand, other.hand = [Card(Rank.ACE, Suit.HEART)], [Card(Rank.KING, Suit.HEART)]
            other.score = 90

        caller_ws = ws_host if caller.id == "p0" else ws_friend
        reply = call(caller_ws, "call_gabo")
        assert reply["state"]["phase"] == "game_over"
        assert reply["state"]["winner_id"] == winner_seat
    return code


def test_online_game_history_is_visible_to_every_player(clients):
    host, friend, stranger = clients
    code = finish_online_game(host, friend, winner="friend")

    for client in (host, friend):
        games = client.get("/api/rooms/history/mine").json()
        assert [g["room_code"] for g in games].count(code) == 1
        game = next(g for g in games if g["room_code"] == code)
        assert game["finished"] is True
        assert {r["player_name"] for r in game["results"]} == {"Hote", "Ami"}

    # Someone who did not play does not see it.
    assert code not in [g["room_code"] for g in stranger.get("/api/rooms/history/mine").json()]


def test_leaderboard_ranks_players_by_wins(clients):
    host, friend, stranger = clients
    finish_online_game(host, friend, winner="friend")
    finish_online_game(host, friend, winner="friend")
    finish_online_game(host, friend, winner="host")

    # Other tests reuse the same display names: identify rows via is_you.
    friend_row = next(e for e in friend.get("/api/rooms/leaderboard").json() if e["is_you"])
    board = host.get("/api/rooms/leaderboard").json()
    host_row = next(e for e in board if e["is_you"])
    assert friend_row["wins"] == 2 and friend_row["games_played"] == 3
    assert host_row["wins"] == 1 and host_row["games_played"] == 3
    assert friend_row["rank"] < host_row["rank"]
    # Ranks follow wins in order.
    assert [e["wins"] for e in board] == sorted((e["wins"] for e in board), reverse=True)
    # Nobody who never finished an online game shows up.
    assert "Inconnu" not in [e["display_name"] for e in board]


def test_local_games_do_not_count_in_leaderboard(clients):
    host, _, _ = clients
    room = host.post("/api/rooms", json={"player_names": ["Alice", "Bob"]}).json()
    before = host.get("/api/rooms/leaderboard").json()
    assert room["mode"] == "local"
    assert all(e["display_name"] not in ("Alice", "Bob") for e in before)


# ---------------------------------------------------------------------------
# Account deletion and admin
# ---------------------------------------------------------------------------

def test_delete_account_requires_password(clients):
    host, _, _ = clients
    r = host.request("DELETE", "/api/auth/me", json={"password": "wrong"})
    assert r.status_code == 401
    assert host.get("/api/auth/me").status_code == 200


def test_deleting_account_keeps_other_players_history(clients):
    host, friend, _ = clients
    code = finish_online_game(host, friend, winner="host")
    host_email = host.get("/api/auth/me").json()["email"]
    local = host.post("/api/rooms", json={"player_names": ["A", "B"]}).json()["code"]

    r = host.request("DELETE", "/api/auth/me", json={"password": "secret123"})
    assert r.status_code == 200
    # Logged out, and the account is really gone.
    assert host.get("/api/auth/me").status_code == 401
    assert host.post("/api/auth/login", json={"email": host_email, "password": "secret123"}).status_code == 401

    # The friend still sees the online game the deleted player created.
    games = friend.get("/api/rooms/history/mine").json()
    game = next(g for g in games if g["room_code"] == code)
    assert {r["player_name"] for r in game["results"]} == {"Hote", "Ami"}
    # The deleted player no longer counts in the leaderboard.
    board = friend.get("/api/rooms/leaderboard").json()
    assert sum(1 for e in board if e["wins"] and e["display_name"] == "Hote" and e["games_played"] == 1) == 0
    friend_row = next(e for e in board if e["is_you"])
    assert friend_row["games_played"] == 1 and friend_row["wins"] == 0

    # The deleted player's local game is gone with the account.
    assert local not in [g["room_code"] for g in friend.get("/api/rooms/history/mine").json()]


def test_admin_area_is_only_for_the_configured_email(clients, monkeypatch):
    host, friend, stranger = clients
    host_email = host.get("/api/auth/me").json()["email"]
    friend_id = friend.get("/api/auth/me").json()["id"]

    monkeypatch.delenv("GABO_ADMIN_EMAILS", raising=False)
    assert host.get("/api/admin/users").status_code == 403

    monkeypatch.setenv("GABO_ADMIN_EMAILS", host_email.upper())
    assert host.get("/api/auth/me").json()["is_admin"] is True
    assert friend.get("/api/auth/me").json()["is_admin"] is False
    assert friend.get("/api/admin/users").status_code == 403
    assert friend.request("DELETE", f"/api/admin/users/{friend_id}").status_code == 403

    users = host.get("/api/admin/users").json()
    assert any(u["id"] == friend_id for u in users)

    me_id = host.get("/api/auth/me").json()["id"]
    assert host.request("DELETE", f"/api/admin/users/{me_id}").status_code == 400
    assert host.request("DELETE", f"/api/admin/users/{friend_id}").status_code == 200
    assert friend.get("/api/auth/me").status_code == 401
    assert all(u["id"] != friend_id for u in host.get("/api/admin/users").json())


def test_admin_api_requires_login(clients):
    host, _, _ = clients
    host.post("/api/auth/logout")
    assert host.get("/api/admin/users").status_code == 401


# ---------------------------------------------------------------------------
# Account settings
# ---------------------------------------------------------------------------

def test_change_display_name_shows_up_in_leaderboard(clients):
    host, friend, _ = clients
    finish_online_game(host, friend, winner="host")
    r = host.patch("/api/auth/me", json={"display_name": "  Champion  "})
    assert r.status_code == 200 and r.json()["display_name"] == "Champion"
    assert host.get("/api/auth/me").json()["display_name"] == "Champion"
    assert next(e for e in friend.get("/api/rooms/leaderboard").json() if e["display_name"] == "Champion")
    assert host.patch("/api/auth/me", json={"display_name": "   "}).status_code == 400
    assert host.patch("/api/auth/me", json={"display_name": ""}).status_code == 422


def test_change_password(clients):
    host, _, _ = clients
    email = host.get("/api/auth/me").json()["email"]
    bad = host.post("/api/auth/password", json={"current_password": "nope", "new_password": "newsecret1"})
    assert bad.status_code == 401
    short = host.post("/api/auth/password", json={"current_password": "secret123", "new_password": "123"})
    assert short.status_code == 422
    ok = host.post("/api/auth/password", json={"current_password": "secret123", "new_password": "newsecret1"})
    assert ok.status_code == 200
    host.post("/api/auth/logout")
    assert host.post("/api/auth/login", json={"email": email, "password": "secret123"}).status_code == 401
    assert host.post("/api/auth/login", json={"email": email, "password": "newsecret1"}).status_code == 200


def test_round_ends_on_its_own_after_someone_runs_out_of_cards(clients, monkeypatch):
    from app.game.cards import Card, Rank, Suit
    import time

    monkeypatch.setattr("app.game.engine.DOUBLES_WINDOW_SECONDS", 0.3)
    host, friend, _ = clients
    code = create_online_room(host, friend)
    with host.websocket_connect(f"/ws/rooms/{code}") as ws_host, \
            friend.websocket_connect(f"/ws/rooms/{code}") as ws_friend:
        ws_host.receive_json()
        ws_friend.receive_json()
        call(ws_host, "start_round")
        call(ws_host, "peek_initial", indices=[0, 1])
        call(ws_friend, "peek_initial", indices=[0, 1])

        engine = room_manager.get(code).engine
        top = engine.deck.top_discard
        host_player, friend_player = engine.players
        host_player.hand = [Card(top.rank, Suit.SPADE if top.suit != Suit.SPADE else Suit.CLUB)]
        friend_player.hand = [Card(Rank.FOUR, Suit.CLUB), Card(Rank.TWO, Suit.CLUB)]
        engine.doubles_window_until = 0  # skip the first window (cards still being memorised)

        reply = call(ws_host, "snap_attempt", hand_index=0)
        assert reply["state"]["phase"] == "final_doubles"
        assert 0 < reply["state"]["final_doubles_remaining"] <= 0.3

        # Nobody else acts: the server ends the round by itself.
        deadline = time.time() + 3
        while engine.phase.value == "final_doubles" and time.time() < deadline:
            time.sleep(0.05)
        assert engine.phase.value == "round_over"  # ended by the server timer
        state = call(ws_friend, "get_state")["state"]
        assert state["phase"] == "round_over"
        assert state["last_round_summary"]["empty_hand_ids"] == ["p0"]
        assert state["last_round_summary"]["score_deltas"] == {"p0": 0, "p1": 6}
