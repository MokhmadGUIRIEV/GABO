from __future__ import annotations

import random
import string
from dataclasses import dataclass, field

from fastapi import WebSocket

from .game.engine import GameEngine


def _generate_code(length: int = 5) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


@dataclass
class RoomSession:
    code: str
    owner_user_id: int
    player_ids: list[str]
    player_names: dict[str, str]
    engine: GameEngine
    mode: str = "local"
    game_record_id: int | None = None
    sockets: list[WebSocket] = field(default_factory=list)

    async def broadcast(self, message: dict) -> None:
        stale = []
        for ws in self.sockets:
            try:
                await ws.send_json(message)
            except Exception:
                stale.append(ws)
        for ws in stale:
            if ws in self.sockets:
                self.sockets.remove(ws)


class RoomManager:
    def __init__(self) -> None:
        self._rooms: dict[str, RoomSession] = {}

    def create_room(self, owner_user_id: int, player_names: list[str], mode: str = "local") -> RoomSession:
        code = _generate_code()
        while code in self._rooms:
            code = _generate_code()
        player_ids = [f"p{i}" for i in range(len(player_names))]
        names = dict(zip(player_ids, player_names))
        engine = GameEngine(player_ids, names)
        room = RoomSession(
            code=code,
            owner_user_id=owner_user_id,
            player_ids=player_ids,
            player_names=names,
            engine=engine,
            mode=mode,
        )
        self._rooms[code] = room
        return room

    def get(self, code: str) -> RoomSession | None:
        return self._rooms.get(code.upper())

    def remove(self, code: str) -> None:
        self._rooms.pop(code.upper(), None)


room_manager = RoomManager()
