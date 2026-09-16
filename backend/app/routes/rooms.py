from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..models import GameRecord, User
from ..rooms import room_manager
from ..schemas import GameRecordOut, RoomCreateRequest, RoomOut

router = APIRouter(prefix="/api/rooms", tags=["rooms"])


@router.post("", response_model=RoomOut)
def create_room(payload: RoomCreateRequest, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    names = [n.strip() for n in payload.player_names if n.strip()]
    if not 2 <= len(names) <= 6:
        raise HTTPException(status_code=400, detail="GABO se joue de 2 à 6 joueurs.")

    room = room_manager.create_room(owner_user_id=user.id, player_names=names)

    record = GameRecord(room_code=room.code, created_by_user_id=user.id, mode=room.mode)
    db.add(record)
    db.commit()
    db.refresh(record)
    room.game_record_id = record.id

    return RoomOut(code=room.code, player_ids=room.player_ids, player_names=room.player_names)


@router.get("/{code}", response_model=RoomOut)
def get_room(code: str, user: User = Depends(get_current_user)):
    room = room_manager.get(code)
    if room is None:
        raise HTTPException(status_code=404, detail="Salle introuvable.")
    return RoomOut(code=room.code, player_ids=room.player_ids, player_names=room.player_names)


@router.get("/history/mine", response_model=list[GameRecordOut])
def my_history(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    records = (
        db.query(GameRecord)
        .filter(GameRecord.created_by_user_id == user.id)
        .order_by(GameRecord.started_at.desc())
        .all()
    )
    return records
