from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..accounts import delete_account, is_admin
from ..db import get_db
from ..deps import get_current_user
from ..models import User
from ..schemas import (
    ChangePasswordRequest,
    DeleteAccountRequest,
    LoginRequest,
    RegisterRequest,
    UpdateProfileRequest,
    UserOut,
)
from ..security import hash_password, verify_password

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _user_out(user: User) -> UserOut:
    return UserOut(id=user.id, email=user.email, display_name=user.display_name, is_admin=is_admin(user))


@router.post("/register", response_model=UserOut)
def register(payload: RegisterRequest, request: Request, db: Session = Depends(get_db)):
    user = User(
        email=payload.email.lower(),
        display_name=payload.display_name,
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Cet email est déjà utilisé.")
    db.refresh(user)
    request.session["user_id"] = user.id
    return _user_out(user)


@router.post("/login", response_model=UserOut)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email.lower()).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Email ou mot de passe incorrect.")
    request.session["user_id"] = user.id
    return _user_out(user)


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return _user_out(user)


@router.patch("/me", response_model=UserOut)
def update_me(payload: UpdateProfileRequest, user: User = Depends(get_current_user),
              db: Session = Depends(get_db)):
    name = payload.display_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Le pseudo ne peut pas être vide.")
    user.display_name = name
    db.commit()
    return _user_out(user)


@router.post("/password")
def change_password(payload: ChangePasswordRequest, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(status_code=401, detail="Mot de passe actuel incorrect.")
    user.password_hash = hash_password(payload.new_password)
    db.commit()
    return {"ok": True}


@router.delete("/me")
def delete_me(payload: DeleteAccountRequest, request: Request,
              user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Re-typing the password guards against a session left open on a shared device.
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=401, detail="Mot de passe incorrect.")
    delete_account(db, user)
    request.session.clear()
    return {"ok": True}
