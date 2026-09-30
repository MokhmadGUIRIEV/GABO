from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..game.engine import GameError
from ..models import GameRecord, User
from ..rooms import MAX_PLAYERS, MIN_PLAYERS, RoomSession, room_manager
from ..schemas import GameRecordOut, RoomCreateRequest, RoomOut

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


def _room_out(room: RoomSession, user: User) -> RoomOut:
    return RoomOut(
        code=room.code,
        mode=room.mode,
        player_ids=room.player_ids,
        player_names=room.player_names,
        started=room.started,
        you=room.player_id_for_user(user.id) if room.mode == "online" else None,
        host_player_id=room.host_player_id,
    )


@router.post("", response_model=RoomOut)
def create_room(payload: RoomCreateRequest, user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    if payload.mode == "online":
        room = room_manager.create_online_room(owner_user_id=user.id, owner_name=user.display_name)
    else:
        names = [n.strip() for n in payload.player_names if n.strip()]
        if not MIN_PLAYERS <= len(names) <= MAX_PLAYERS:
            raise HTTPException(status_code=400, detail="GABO se joue de 2 à 6 joueurs.")
        room = room_manager.create_local_room(owner_user_id=user.id, player_names=names)

    record = GameRecord(room_code=room.code, created_by_user_id=user.id, mode=room.mode)
    db.add(record)
    db.commit()
    db.refresh(record)
    room.game_record_id = record.id

    return _room_out(room, user)


@router.get("/history/mine", response_model=list[GameRecordOut])
def my_history(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    records = (
        db.query(GameRecord)
        .filter(GameRecord.created_by_user_id == user.id)
        .order_by(GameRecord.started_at.desc())
        .all()
    )
    return records


@router.get("/{code}", response_model=RoomOut)
def get_room(code: str, user: User = Depends(get_current_user)):
    room = room_manager.get(code)
    if room is None:
        raise HTTPException(status_code=404, detail="Salle introuvable.")
    return _room_out(room, user)


@router.post("/{code}/join", response_model=RoomOut)
async def join_room(code: str, user: User = Depends(get_current_user)):
    room = room_manager.get(code)
    if room is None:
        raise HTTPException(status_code=404, detail="Salle introuvable.")
    if room.mode != "online":
        raise HTTPException(status_code=400, detail="Cette partie se joue sur un seul appareil.")
    already_in = room.player_id_for_user(user.id) is not None
    try:
        room.claim_seat(user.id, user.display_name)
    except GameError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not already_in:
        await room.broadcast({"type": "table_updated"})
    return _room_out(room, user)
