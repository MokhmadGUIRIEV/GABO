from __future__ import annotations

import datetime

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=6, max_length=200)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: int
    email: str
    display_name: str

    class Config:
        from_attributes = True


class RoomCreateRequest(BaseModel):
    player_names: list[str] = Field(min_length=2, max_length=6)


class RoomOut(BaseModel):
    code: str
    player_ids: list[str]
    player_names: dict[str, str]


class PlayerResultOut(BaseModel):
    player_name: str
    final_score: int
    is_winner: bool
    eliminated: bool

    class Config:
        from_attributes = True


class GameRecordOut(BaseModel):
    id: int
    room_code: str
    mode: str
    rounds_played: int
    started_at: datetime.datetime
    finished_at: datetime.datetime | None
    finished: bool
    results: list[PlayerResultOut]

    class Config:
        from_attributes = True
