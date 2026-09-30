from __future__ import annotations

import random
import string
from dataclasses import dataclass, field

from fastapi import WebSocket

from .game.engine import GameEngine, GameError

MAX_PLAYERS = 6
MIN_PLAYERS = 2


def _generate_code(length: int = 5) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


@dataclass
class RoomSession:
    """A game table.

    - "local" mode: one trusted browser (the owner's) drives every seat, the
      players are just names typed in at creation.
    - "online" mode: every seat belongs to a user account playing from their
      own device. The engine is only built when the host starts the game,
      from the seats actually claimed at that point.
    """

    code: str
    owner_user_id: int
    player_ids: list[str]
    player_names: dict[str, str]
    engine: GameEngine | None
    mode: str = "local"
    game_record_id: int | None = None
    seat_user_ids: dict[str, int] = field(default_factory=dict)
    sockets: list[WebSocket] = field(default_factory=list)

    @property
    def started(self) -> bool:
        return self.engine is not None

    @property
    def host_player_id(self) -> str | None:
        if self.mode != "online":
            return None
        return self.player_id_for_user(self.owner_user_id)

    def player_id_for_user(self, user_id: int | None) -> str | None:
        for player_id, uid in self.seat_user_ids.items():
            if uid == user_id:
                return player_id
        return None

    def claim_seat(self, user_id: int, display_name: str) -> str:
        existing = self.player_id_for_user(user_id)
        if existing is not None:
            return existing
        if self.started:
            raise GameError("La partie a déjà commencé.")
        if len(self.player_ids) >= MAX_PLAYERS:
            raise GameError("La salle est complète.")
        player_id = f"p{len(self.player_ids)}"
        self.player_ids.append(player_id)
        self.player_names[player_id] = display_name
        self.seat_user_ids[player_id] = user_id
        return player_id

    def start_engine(self) -> GameEngine:
        if self.engine is None:
            if len(self.player_ids) < MIN_PLAYERS:
                raise GameError("Il faut au moins 2 joueurs pour lancer la partie.")
            self.engine = GameEngine(list(self.player_ids), dict(self.player_names))
        return self.engine

    def state_for(self, viewer_id: str | None) -> dict:
        if self.engine is not None:
            return self.engine.public_state(viewer_id)
        # Waiting room: same shape as GameEngine.public_state(), no cards yet.
        return {
            "phase": "lobby",
            "round_number": 0,
            "current_player_id": None,
            "gabo_caller_id": None,
            "draw_pile_count": 0,
            "top_discard": None,
            "drawn_card": None,
            "pending_power": None,
            "pending_power_owner": None,
            "initial_peek_deadline": None,
            "players_peeked": [],
            "players": [
                {"id": pid, "name": self.player_names[pid], "score": 0, "eliminated": False,
                 "hand_size": 0, "hand": []}
                for pid in self.player_ids
            ],
            "winner_id": None,
            "last_round_summary": None,
        }

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

    def _new_code(self) -> str:
        code = _generate_code()
        while code in self._rooms:
            code = _generate_code()
        return code

    def create_local_room(self, owner_user_id: int, player_names: list[str]) -> RoomSession:
        player_ids = [f"p{i}" for i in range(len(player_names))]
        names = dict(zip(player_ids, player_names))
        room = RoomSession(
            code=self._new_code(),
            owner_user_id=owner_user_id,
            player_ids=player_ids,
            player_names=names,
            engine=GameEngine(player_ids, names),
            mode="local",
        )
        self._rooms[room.code] = room
        return room

    def create_online_room(self, owner_user_id: int, owner_name: str) -> RoomSession:
        room = RoomSession(
            code=self._new_code(),
            owner_user_id=owner_user_id,
            player_ids=[],
            player_names={},
            engine=None,
            mode="online",
        )
        room.claim_seat(owner_user_id, owner_name)
        self._rooms[room.code] = room
        return room

    def get(self, code: str) -> RoomSession | None:
        return self._rooms.get(code.upper())

    def remove(self, code: str) -> None:
        self._rooms.pop(code.upper(), None)


room_manager = RoomManager()
