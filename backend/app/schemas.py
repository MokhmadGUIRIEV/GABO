from __future__ import annotations

import datetime
from typing import Literal

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
    is_admin: bool = False

    class Config:
        from_attributes = True


class DeleteAccountRequest(BaseModel):
    password: str


class AdminUserOut(BaseModel):
    id: int
    email: str
    display_name: str
    created_at: datetime.datetime
    games_played: int
    wins: int
    is_admin: bool


class RoomCreateRequest(BaseModel):
    mode: Literal["local", "online"] = "local"
    player_names: list[str] = Field(default_factory=list, max_length=6)


class RoomOut(BaseModel):
    code: str
    mode: str
    player_ids: list[str]
    player_names: dict[str, str]
    started: bool
    you: str | None = None
    host_player_id: str | None = None


class PlayerResultOut(BaseModel):
    player_name: str
    final_score: int
    is_winner: bool
    eliminated: bool

    class Config:
        from_attributes = True


class LeaderboardEntryOut(BaseModel):
    rank: int
    display_name: str
    wins: int
    games_played: int
    is_you: bool


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
