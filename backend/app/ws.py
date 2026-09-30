from __future__ import annotations

import asyncio
import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from .db import SessionLocal
from .game.engine import GameError, Phase
from .models import GameRecord, PlayerResult
from .rooms import RoomSession, room_manager

router = APIRouter()

# Online games: if someone never picks their 2 starting cards (AFK, closed
# tab...), the round still starts after this delay instead of blocking.
ONLINE_INITIAL_PEEK_TIMEOUT_SECONDS = 45

HOST_ONLY_ACTIONS = {"start_round", "finish_initial_peek"}


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
                user_id=room.seat_user_ids.get(player.id),
                player_name=player.name,
                final_score=player.score,
                is_winner=(player.id == room.engine.winner_id),
                eliminated=player.eliminated,
            ))
        db.commit()
    finally:
        db.close()


async def _auto_finish_initial_peek(room: RoomSession, round_number: int) -> None:
    await asyncio.sleep(ONLINE_INITIAL_PEEK_TIMEOUT_SECONDS)
    engine = room.engine
    if engine is not None and engine.round_number == round_number and engine.phase == Phase.INITIAL_PEEK:
        engine.finish_initial_peek()
        await room.broadcast({"type": "table_updated"})


def _dispatch(room: RoomSession, msg: dict) -> None:
    action = msg.get("action")

    if action == "get_state":
        return

    if action == "start_round":
        engine = room.start_engine()
        engine.start_round()
        if room.mode == "online":
            asyncio.create_task(_auto_finish_initial_peek(room, engine.round_number))
        return

    engine = room.engine
    if engine is None:
        raise GameError("La partie n'a pas encore commencé.")

    if action == "peek_initial":
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
    else:
        raise GameError(f"Action inconnue: {action}")


async def _refuse(websocket: WebSocket, code: int) -> None:
    # Accept first: a close frame sent before the handshake completes never
    # reaches the browser, which then can't tell why it was refused.
    await websocket.accept()
    await websocket.close(code=code)


@router.websocket("/ws/rooms/{code}")
async def room_websocket(websocket: WebSocket, code: str):
    room = room_manager.get(code)
    if room is None:
        await _refuse(websocket, 4404)
        return

    session_user_id = websocket.session.get("user_id")
    if room.mode == "online":
        # Each connection is bound to the seat its logged-in user claimed.
        bound_player_id = room.player_id_for_user(session_user_id)
        if bound_player_id is None:
            await _refuse(websocket, 4403)
            return
    else:
        # Local "pass & play": the owner's single browser drives every seat.
        bound_player_id = None
        if session_user_id != room.owner_user_id:
            await _refuse(websocket, 4401)
            return

    await websocket.accept()
    room.sockets.append(websocket)
    try:
        await websocket.send_json({"type": "table_updated", "you": bound_player_id})
        while True:
            msg = await websocket.receive_json()
            request_id = msg.get("id")
            action = msg.get("action")

            if room.mode == "online":
                # Never trust the client about who is acting or whose view it
                # gets: both always come from the authenticated connection.
                msg["player_id"] = bound_player_id
                viewer_id = bound_player_id
            else:
                viewer_id = msg.get("viewer_id", msg.get("player_id"))

            try:
                if (room.mode == "online" and action in HOST_ONLY_ACTIONS
                        and bound_player_id != room.host_player_id):
                    raise GameError("Seul l'hôte de la partie peut faire ça.")
                _dispatch(room, msg)
                await websocket.send_json({
                    "type": "state",
                    "id": request_id,
                    "you": bound_player_id,
                    "viewer_id": viewer_id,
                    "state": room.state_for(viewer_id) if viewer_id else None,
                })
                if action != "get_state":
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
