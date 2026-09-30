from __future__ import annotations

import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Boolean
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100))
    password_hash: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)

    games: Mapped[list["GameRecord"]] = relationship(back_populates="created_by", cascade="all, delete-orphan")


class GameRecord(Base):
    __tablename__ = "game_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    room_code: Mapped[str] = mapped_column(String(16), index=True)
    created_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    mode: Mapped[str] = mapped_column(String(20), default="local")
    rounds_played: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime, default=datetime.datetime.utcnow)
    finished_at: Mapped[datetime.datetime | None] = mapped_column(DateTime, nullable=True)
    finished: Mapped[bool] = mapped_column(Boolean, default=False)

    created_by: Mapped["User"] = relationship(back_populates="games")
    results: Mapped[list["PlayerResult"]] = relationship(back_populates="game", cascade="all, delete-orphan")


class PlayerResult(Base):
    __tablename__ = "player_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("game_records.id"))
    # Set for online games (each seat is an account); None for local seats,
    # which are just names typed in by the device owner.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True, index=True)
    player_name: Mapped[str] = mapped_column(String(100))
    final_score: Mapped[int] = mapped_column(Integer, default=0)
    is_winner: Mapped[bool] = mapped_column(Boolean, default=False)
    eliminated: Mapped[bool] = mapped_column(Boolean, default=False)

    game: Mapped["GameRecord"] = relationship(back_populates="results")
