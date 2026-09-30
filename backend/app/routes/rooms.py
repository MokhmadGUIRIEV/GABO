from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func, or_, select
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_current_user
from ..game.engine import GameError
from ..models import GameRecord, PlayerResult, User
from ..rooms import MAX_PLAYERS, MIN_PLAYERS, RoomSession, room_manager
from ..schemas import GameRecordOut, LeaderboardEntryOut, RoomCreateRequest, RoomOut

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
    # Games I created, plus online games I played in (created by someone else).
    played_in = select(PlayerResult.game_id).where(PlayerResult.user_id == user.id)
    records = (
        db.query(GameRecord)
        .filter(or_(GameRecord.created_by_user_id == user.id, GameRecord.id.in_(played_in)))
        .order_by(GameRecord.started_at.desc())
        .all()
    )
    return records


@router.get("/leaderboard", response_model=list[LeaderboardEntryOut])
def leaderboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Only results tied to an account count, i.e. finished online games:
    # local seats are just names typed in on one device.
    wins = func.sum(case((PlayerResult.is_winner, 1), else_=0))
    rows = (
        db.query(User.id, User.display_name, wins.label("wins"), func.count(PlayerResult.id).label("played"))
        .join(PlayerResult, PlayerResult.user_id == User.id)
        .group_by(User.id, User.display_name)
        .all()
    )
    # Most wins first; on equal wins, fewer games played (better ratio) first.
    rows.sort(key=lambda r: (-(r.wins or 0), r.played, r.display_name.lower()))
    entries = []
    for i, r in enumerate(rows):
        wins_count = int(r.wins or 0)
        # Players with the same number of wins share the same rank (1, 1, 3...).
        rank = entries[-1].rank if entries and entries[-1].wins == wins_count else i + 1
        entries.append(LeaderboardEntryOut(
            rank=rank, display_name=r.display_name, wins=wins_count,
            games_played=r.played, is_you=(r.id == user.id),
        ))
    return entries


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
