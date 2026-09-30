from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from ..accounts import delete_account, is_admin
from ..db import get_db
from ..deps import get_admin_user
from ..models import PlayerResult, User
from ..schemas import AdminUserOut

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/users", response_model=list[AdminUserOut])
def list_users(_: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    wins = func.coalesce(func.sum(case((PlayerResult.is_winner, 1), else_=0)), 0)
    rows = (
        db.query(User, func.count(PlayerResult.id), wins)
        .outerjoin(PlayerResult, PlayerResult.user_id == User.id)
        .group_by(User.id)
        .order_by(User.created_at.desc())
        .all()
    )
    return [
        AdminUserOut(
            id=u.id, email=u.email, display_name=u.display_name, created_at=u.created_at,
            games_played=played, wins=int(w), is_admin=is_admin(u),
        )
        for u, played, w in rows
    ]


@router.delete("/users/{user_id}")
def delete_user(user_id: int, admin: User = Depends(get_admin_user), db: Session = Depends(get_db)):
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="Supprime ton propre compte depuis « Mon compte ».")
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="Compte introuvable.")
    delete_account(db, user)
    return {"ok": True}
