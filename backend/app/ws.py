from __future__ import annotations

import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .db import SessionLocal
from .game.engine import GameError, Phase
from .models import GameRecord, PlayerResult
from .rooms import RoomSession, room_manager

router = APIRouter()


def _persist_game_over(room: RoomSession) -> None:
    if room.game_record_id is None:
        return
    db = SessionLocal()
    try:
        record = db.get(GameRecord, room.game_record_id)
        if record is None or record.finished:
            return
        record.finished = True
        record.finished_at = datetime.datetime.utcnow()
        record.rounds_played = room.engine.round_number
        for player in room.engine.players:
            db.add(PlayerResult(
                game_id=record.id,
                player_name=player.name,
                final_score=player.score,
                is_winner=(player.id == room.engine.winner_id),
                eliminated=player.eliminated,
            ))
        db.commit()
    finally:
        db.close()


def _dispatch(room: RoomSession, msg: dict) -> None:
    engine = room.engine
    action = msg.get("action")

    if action == "start_round":
        engine.start_round()
    elif action == "peek_initial":
        engine.peek_initial(msg["player_id"], msg["indices"])
        active_ids = {p.id for p in engine.players if not p.eliminated}
        if active_ids.issubset(set(engine.public_state("__public__")["players_peeked"])):
            engine.finish_initial_peek()
    elif action == "finish_initial_peek":
        engine.finish_initial_peek()
    elif action == "call_gabo":
        engine.call_gabo(msg["player_id"])
        if engine.phase == Phase.GAME_OVER:
            _persist_game_over(room)
    elif action == "draw_card":
        engine.draw_card(msg["player_id"])
    elif action == "discard_drawn":
        engine.discard_drawn(msg["player_id"])
    elif action == "swap_drawn":
        engine.swap_drawn(msg["player_id"], msg["hand_index"])
    elif action == "use_power_peek_own":
        engine.use_power_peek_own(msg["player_id"], msg["hand_index"])
    elif action == "use_power_peek_opponent":
        engine.use_power_peek_opponent(msg["player_id"], msg["target_player_id"], msg["hand_index"])
    elif action == "use_power_swap_and_peek":
        engine.use_power_swap_and_peek(
            msg["player_id"], msg["own_index"], msg["target_player_id"], msg["target_index"]
        )
    elif action == "skip_power":
        engine.skip_power(msg["player_id"])
    elif action == "snap_attempt":
        engine.snap_attempt(msg["player_id"], msg["hand_index"])
    elif action == "get_state":
        pass
    else:
        raise GameError(f"Action inconnue: {action}")


@router.websocket("/ws/rooms/{code}")
async def room_websocket(websocket: WebSocket, code: str):
    room = room_manager.get(code)
    if room is None:
        await websocket.close(code=4404)
        return

    session_user_id = websocket.session.get("user_id")
    if session_user_id != room.owner_user_id:
        await websocket.close(code=4401)
        return

    await websocket.accept()
    room.sockets.append(websocket)
    try:
        await websocket.send_json({"type": "table_updated"})
        while True:
            msg = await websocket.receive_json()
            viewer_id = msg.get("viewer_id", msg.get("player_id"))
            request_id = msg.get("id")
            try:
                _dispatch(room, msg)
                await websocket.send_json({
                    "type": "state",
                    "id": request_id,
                    "viewer_id": viewer_id,
                    "state": room.engine.public_state(viewer_id) if viewer_id else None,
                })
                if msg.get("action") != "get_state":
                    await room.broadcast({"type": "table_updated"})
            except GameError as exc:
                await websocket.send_json({"type": "error", "id": request_id, "message": str(exc)})
            except KeyError as exc:
                await websocket.send_json({"type": "error", "id": request_id, "message": f"Champ manquant: {exc}"})
    except WebSocketDisconnect:
        pass
    finally:
        if websocket in room.sockets:
            room.sockets.remove(websocket)
