"""Pure game-logic engine for GABO.

This module has no knowledge of HTTP, WebSockets, or any transport layer so
it can be reused unchanged whether players share one device (local
"pass & play") or connect from separate devices (future online mode). All
public/private information rules are enforced here via `public_state()`,
which returns a view of the game scoped to a single viewing player.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from .cards import (
    Card,
    Deck,
    GABO_PENALTY,
    ELIMINATION_SCORE,
    EXACT_LIMIT_RESET_SCORE,
    DOUBLES_WINDOW_SECONDS,
    INITIAL_HAND_SIZE,
    INITIAL_PEEK_COUNT,
    PEEK_DURATION_SECONDS,
)


class GameError(Exception):
    """Raised when a player attempts an action that is not currently legal."""


class Phase(str, Enum):
    LOBBY = "lobby"
    INITIAL_PEEK = "initial_peek"
    TURN = "turn"
    AWAITING_DECISION = "awaiting_decision"
    POWER_PENDING = "power_pending"
    # Someone has no cards left: the round ends after a short last chance
    # for everyone else to drop their own doubles.
    FINAL_DOUBLES = "final_doubles"
    ROUND_OVER = "round_over"
    GAME_OVER = "game_over"


class PowerType(str, Enum):
    PEEK_OWN = "peek_own"
    PEEK_OPPONENT = "peek_opponent"
    SWAP_AND_PEEK = "swap_and_peek"


@dataclass
class Player:
    id: str
    name: str
    hand: list[Card] = field(default_factory=list)
    score: int = 0
    eliminated: bool = False

    @property
    def hand_value(self) -> int:
        return sum(card.value for card in self.hand)


@dataclass
class _Reveal:
    viewer_id: str
    target_player_id: str
    hand_index: int
    card: Card
    expires_at: float


# Every recent move, readable by everyone, so each screen can show what just
# happened (which cards moved, from where). Only what the whole table would
# see on a real table: the cards' faces stay private except those that are
# put face up on the discard pile.
MAX_RECENT_ACTIONS = 30


class GameEngine:
    """State machine for a single GABO table.

    `players` order is fixed for the lifetime of the table and defines
    turn order (clockwise = list order, wrapping around). Eliminated
    players are skipped when computing turns but keep their score and
    remain in `players` so the history/UI can still show them.
    """

    def __init__(self, player_ids: list[str], player_names: dict[str, str],
                 rng_seed: int | None = None, clock=time.time,
                 doubles_window_seconds: float | None = None):
        if not 2 <= len(player_ids) <= 6:
            raise GameError("GABO se joue de 2 à 6 joueurs.")
        self._clock = clock
        self.doubles_window_seconds = (
            DOUBLES_WINDOW_SECONDS if doubles_window_seconds is None else doubles_window_seconds
        )
        self.players: list[Player] = [
            Player(id=pid, name=player_names.get(pid, pid)) for pid in player_ids
        ]
        self.deck = Deck(rng=__import__("random").Random(rng_seed))
        self.phase: Phase = Phase.LOBBY
        self.round_number = 0
        self._starting_index = 0
        self.current_index = 0
        self.gabo_caller_id: str | None = None
        self.drawn_card: Card | None = None
        self.drawn_card_owner: str | None = None
        self.pending_power: PowerType | None = None
        self.pending_power_owner: str | None = None
        # The face-up card whose power is being used (shown to everyone).
        self.pending_power_card: Card | None = None
        self.initial_peek_deadline: float | None = None
        self._peeked_players: set[str] = set()
        self._reveals: list[_Reveal] = []
        self.winner_id: str | None = None
        self.last_round_summary: dict | None = None
        # Nobody may draw or call GABO before this time (doubles window).
        self.doubles_window_until: float = 0.0
        # FINAL_DOUBLES phase: when the round actually ends.
        self.final_doubles_deadline: float | None = None
        self.actions: list[dict] = []
        self._action_seq = 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _player(self, player_id: str) -> Player:
        for p in self.players:
            if p.id == player_id:
                return p
        raise GameError(f"Joueur inconnu: {player_id}")

    def _active_players(self) -> list[Player]:
        return [p for p in self.players if not p.eliminated]

    @property
    def current_player(self) -> Player:
        return self.players[self.current_index]

    def _add_reveal(self, viewer_id: str, target_player_id: str, hand_index: int, card: Card) -> None:
        self._reveals.append(_Reveal(
            viewer_id=viewer_id,
            target_player_id=target_player_id,
            hand_index=hand_index,
            card=card,
            expires_at=self._clock() + PEEK_DURATION_SECONDS,
        ))

    def _purge_expired_reveals(self) -> None:
        now = self._clock()
        self._reveals = [r for r in self._reveals if r.expires_at > now]

    def _record(self, kind: str, player_id: str, **details) -> None:
        self._action_seq += 1
        self.actions.append({"seq": self._action_seq, "type": kind, "player_id": player_id, **details})
        del self.actions[:-MAX_RECENT_ACTIONS]

    def _open_doubles_window(self) -> None:
        self.doubles_window_until = max(self.doubles_window_until, self._clock() + self.doubles_window_seconds)

    def _require_doubles_window_closed(self) -> None:
        if self._clock() < self.doubles_window_until:
            raise GameError("Attends un instant : les autres peuvent encore poser un doublon.")

    def _advance_turn(self) -> None:
        active = self._active_players()
        if len(active) <= 1:
            return
        n = len(self.players)
        for step in range(1, n + 1):
            candidate = (self.current_index + step) % n
            if not self.players[candidate].eliminated:
                self.current_index = candidate
                return

    def _end_turn(self) -> None:
        self.drawn_card = None
        self.drawn_card_owner = None
        self.pending_power = None
        self.pending_power_owner = None
        if self.gabo_caller_id is not None:
            self._resolve_round()
            return
        self._advance_turn()
        self.phase = Phase.TURN
        self._open_doubles_window()

    # ------------------------------------------------------------------
    # Round lifecycle
    # ------------------------------------------------------------------

    def start_round(self) -> None:
        if self.phase not in (Phase.LOBBY, Phase.ROUND_OVER):
            raise GameError("Impossible de démarrer une nouvelle manche maintenant.")
        active = self._active_players()
        if len(active) <= 1:
            raise GameError("La partie est terminée.")

        self.deck.reset()
        self._reveals.clear()
        self._peeked_players.clear()
        self.gabo_caller_id = None
        self.drawn_card = None
        self.drawn_card_owner = None
        self.pending_power = None
        self.pending_power_owner = None
        self.last_round_summary = None
        self.doubles_window_until = 0.0
        self.final_doubles_deadline = None

        for player in self.players:
            player.hand = []
        for player in active:
            player.hand = [self.deck.draw() for _ in range(INITIAL_HAND_SIZE)]

        self.round_number += 1
        if self.round_number == 1:
            import random
            self._starting_index = random.randrange(len(self.players))
        else:
            n = len(self.players)
            for step in range(1, n + 1):
                candidate = (self._starting_index + step) % n
                if not self.players[candidate].eliminated:
                    self._starting_index = candidate
                    break
        self.current_index = self._starting_index

        self.phase = Phase.INITIAL_PEEK
        self.initial_peek_deadline = self._clock() + PEEK_DURATION_SECONDS

    def peek_initial(self, player_id: str, hand_indices: list[int]) -> None:
        if self.phase != Phase.INITIAL_PEEK:
            raise GameError("Ce n'est pas la phase d'observation initiale.")
        if player_id in self._peeked_players:
            raise GameError("Vous avez déjà observé vos cartes.")
        player = self._player(player_id)
        if player.eliminated:
            raise GameError("Joueur éliminé.")
        if len(set(hand_indices)) != INITIAL_PEEK_COUNT:
            raise GameError(f"Vous devez choisir {INITIAL_PEEK_COUNT} cartes distinctes.")
        for idx in hand_indices:
            if not (0 <= idx < len(player.hand)):
                raise GameError("Index de carte invalide.")
            self._add_reveal(player_id, player_id, idx, player.hand[idx])
        self._peeked_players.add(player_id)

    def finish_initial_peek(self) -> None:
        """Called by the transport layer once the 5s window has elapsed."""
        if self.phase != Phase.INITIAL_PEEK:
            raise GameError("Ce n'est pas la phase d'observation initiale.")
        self.phase = Phase.TURN
        self.initial_peek_deadline = None
        if self.deck.top_discard is None:
            # Retourne la première carte de la défausse : les joueurs peuvent
            # déjà tenter un snap dessus avant même que le premier tour soit joué.
            self.deck.discard(self.deck.draw())
            if self.doubles_window_seconds > 0:
                # Players are still memorising their 2 cards: the doubles
                # window on this first card starts once they're hidden again.
                peek_ends = max((r.expires_at for r in self._reveals), default=self._clock())
                self.doubles_window_until = max(self._clock(), peek_ends) + self.doubles_window_seconds

    # ------------------------------------------------------------------
    # Turn actions
    # ------------------------------------------------------------------

    def call_gabo(self, player_id: str) -> None:
        if self.phase != Phase.TURN:
            raise GameError("Vous ne pouvez dire GABO qu'au début de votre tour.")
        if self.current_player.id != player_id:
            raise GameError("Ce n'est pas votre tour.")
        if self.gabo_caller_id is not None:
            raise GameError("GABO a déjà été annoncé.")
        self._require_doubles_window_closed()
        self.gabo_caller_id = player_id
        self._record("gabo", player_id)
        self._resolve_round()

    def draw_card(self, player_id: str) -> Card:
        if self.phase != Phase.TURN:
            raise GameError("Ce n'est pas le moment de piocher.")
        if self.current_player.id != player_id:
            raise GameError("Ce n'est pas votre tour.")
        self._require_doubles_window_closed()
        card = self.deck.draw()
        self.drawn_card = card
        self.drawn_card_owner = player_id
        self.phase = Phase.AWAITING_DECISION
        self._record("draw", player_id)
        return card

    def discard_drawn(self, player_id: str) -> PowerType | None:
        self._require_decision(player_id)
        card = self.drawn_card
        self.deck.discard(card)
        self._record("discard_drawn", player_id, card=card.to_dict())
        if card.grants_peek_own:
            self.pending_power = PowerType.PEEK_OWN
        elif card.grants_peek_opponent:
            self.pending_power = PowerType.PEEK_OPPONENT
        elif card.grants_swap_and_peek:
            self.pending_power = PowerType.SWAP_AND_PEEK
        else:
            self.pending_power = None

        if self.pending_power is not None:
            self.pending_power_owner = player_id
            self.pending_power_card = card
            self.phase = Phase.POWER_PENDING
            self.drawn_card = None
            self.drawn_card_owner = None
            return self.pending_power
        self._end_turn()
        return None

    def swap_drawn(self, player_id: str, hand_index: int) -> Card:
        self._require_decision(player_id)
        player = self._player(player_id)
        if not (0 <= hand_index < len(player.hand)):
            raise GameError("Index de carte invalide.")
        old_card = player.hand[hand_index]
        player.hand[hand_index] = self.drawn_card
        self.deck.discard(old_card)
        self._record("swap_drawn", player_id, hand_index=hand_index, discarded=old_card.to_dict())
        # Swapping always cancels any power, even for 7/8/9/10/J/Q.
        self._end_turn()
        return old_card

    def _require_decision(self, player_id: str) -> None:
        if self.phase != Phase.AWAITING_DECISION:
            raise GameError("Aucune carte piochée en attente de décision.")
        if self.drawn_card_owner != player_id:
            raise GameError("Ce n'est pas votre carte piochée.")

    # ------------------------------------------------------------------
    # Powers
    # ------------------------------------------------------------------

    def _require_power(self, player_id: str, power: PowerType) -> None:
        if self.phase != Phase.POWER_PENDING:
            raise GameError("Aucun pouvoir en attente.")
        if self.pending_power_owner != player_id:
            raise GameError("Ce n'est pas votre pouvoir.")
        if self.pending_power != power:
            raise GameError("Pouvoir invalide pour la carte défaussée.")

    def skip_power(self, player_id: str) -> None:
        if self.phase != Phase.POWER_PENDING:
            raise GameError("Aucun pouvoir en attente.")
        if self.pending_power_owner != player_id:
            raise GameError("Ce n'est pas votre pouvoir.")
        self._record("skip_power", player_id, power=self.pending_power.value,
                     power_card=self.pending_power_card.to_dict())
        self._end_turn()

    def use_power_peek_own(self, player_id: str, hand_index: int) -> Card:
        self._require_power(player_id, PowerType.PEEK_OWN)
        player = self._player(player_id)
        if not (0 <= hand_index < len(player.hand)):
            raise GameError("Index de carte invalide.")
        card = player.hand[hand_index]
        self._add_reveal(player_id, player_id, hand_index, card)
        self._record("power_peek", player_id, power=self.pending_power.value,
                     power_card=self.pending_power_card.to_dict(), target_id=player_id, hand_index=hand_index)
        self._end_turn()
        return card

    def use_power_peek_opponent(self, player_id: str, target_player_id: str, hand_index: int) -> Card:
        self._require_power(player_id, PowerType.PEEK_OPPONENT)
        if target_player_id == player_id:
            raise GameError("Vous devez cibler un adversaire.")
        target = self._player(target_player_id)
        if not (0 <= hand_index < len(target.hand)):
            raise GameError("Index de carte invalide.")
        card = target.hand[hand_index]
        self._add_reveal(player_id, target_player_id, hand_index, card)
        self._record("power_peek", player_id, power=self.pending_power.value,
                     power_card=self.pending_power_card.to_dict(), target_id=target_player_id,
                     hand_index=hand_index)
        self._end_turn()
        return card

    def use_power_swap_and_peek(self, player_id: str, own_index: int,
                                 target_player_id: str, target_index: int) -> Card:
        self._require_power(player_id, PowerType.SWAP_AND_PEEK)
        if target_player_id == player_id:
            raise GameError("Vous devez cibler un adversaire.")
        player = self._player(player_id)
        target = self._player(target_player_id)
        if not (0 <= own_index < len(player.hand)):
            raise GameError("Index de carte invalide (vous).")
        if not (0 <= target_index < len(target.hand)):
            raise GameError("Index de carte invalide (adversaire).")
        player.hand[own_index], target.hand[target_index] = (
            target.hand[target_index],
            player.hand[own_index],
        )
        new_card = player.hand[own_index]
        self._add_reveal(player_id, player_id, own_index, new_card)
        self._record("power_swap", player_id, power=self.pending_power.value,
                     power_card=self.pending_power_card.to_dict(), own_index=own_index,
                     target_id=target_player_id, target_index=target_index)
        self._end_turn()
        return new_card

    # ------------------------------------------------------------------
    # Snap (out-of-turn discard matching)
    # ------------------------------------------------------------------

    def snap_attempt(self, player_id: str, hand_index: int) -> bool:
        if self.phase in (Phase.LOBBY, Phase.INITIAL_PEEK, Phase.ROUND_OVER, Phase.GAME_OVER):
            raise GameError("Impossible de poser un doublon maintenant.")
        top = self.deck.top_discard
        if top is None:
            raise GameError("La pile de défausse est vide.")
        player = self._player(player_id)
        if player.eliminated:
            raise GameError("Joueur éliminé.")
        if not (0 <= hand_index < len(player.hand)):
            raise GameError("Index de carte invalide.")

        candidate = player.hand[hand_index]
        if candidate.rank == top.rank:
            del player.hand[hand_index]
            self.deck.discard(candidate)
            self._record("snap", player_id, success=True, hand_index=hand_index, card=candidate.to_dict())
            # The pile changed: give everyone time to answer this card too.
            self._open_doubles_window()
            if not player.hand:
                self._start_final_doubles()
            elif self.phase == Phase.FINAL_DOUBLES:
                self.final_doubles_deadline = self.doubles_window_until
            return True
        # Wrong guess: penalty card, drawn blind and added unseen to hand.
        # The card tried is shown to everyone, then goes back face down.
        penalty_card = self.deck.draw()
        player.hand.append(penalty_card)
        self._record("snap", player_id, success=False, hand_index=hand_index, card=candidate.to_dict(),
                     penalty_index=len(player.hand) - 1)
        return False

    def _start_final_doubles(self) -> None:
        """Someone just got rid of their last card: the round ends after one
        last doubles window, whatever the current player was doing."""
        if self.phase == Phase.FINAL_DOUBLES:
            self.final_doubles_deadline = self.doubles_window_until
            return
        if self.drawn_card is not None:
            # The turn in progress is cancelled: the drawn card goes back.
            self.deck.draw_pile.append(self.drawn_card)
        self.drawn_card = None
        self.drawn_card_owner = None
        self.pending_power = None
        self.pending_power_owner = None
        self.phase = Phase.FINAL_DOUBLES
        self.final_doubles_deadline = self.doubles_window_until

    def final_doubles_seconds_left(self) -> float | None:
        if self.phase != Phase.FINAL_DOUBLES or self.final_doubles_deadline is None:
            return None
        return max(0.0, self.final_doubles_deadline - self._clock())

    def finish_final_doubles(self) -> bool:
        """Ends the round once the last doubles window is over. Safe to call at
        any time (the transport layer calls it on a timer): returns whether
        the round ended."""
        if self.phase != Phase.FINAL_DOUBLES or self._clock() < self.final_doubles_deadline:
            return False
        self.final_doubles_deadline = None
        self._resolve_round()
        return True

    # ------------------------------------------------------------------
    # Scoring
    # ------------------------------------------------------------------

    def _resolve_round(self) -> None:
        self._purge_expired_reveals()
        active = self._active_players()
        sums = {p.id: p.hand_value for p in active}
        caller_id = self.gabo_caller_id
        empty_hand_ids = [p.id for p in active if not p.hand]
        caller_wins = False

        deltas: dict[str, int] = {}
        if caller_id is None:
            # Round ended because someone ran out of cards: whoever has no
            # card left scores 0, everyone else adds up their cards.
            for pid, s in sums.items():
                deltas[pid] = 0 if pid in empty_hand_ids else s
        elif all(sums[caller_id] < s for pid, s in sums.items() if pid != caller_id):
            caller_wins = True
            deltas[caller_id] = 0
            for pid, s in sums.items():
                if pid != caller_id:
                    deltas[pid] = s
        else:
            deltas[caller_id] = GABO_PENALTY
            for pid in sums:
                if pid != caller_id:
                    deltas[pid] = 0

        newly_eliminated = []
        reset_to_50 = []
        for p in active:
            p.score += deltas[p.id]
            if p.score == ELIMINATION_SCORE:
                p.score = EXACT_LIMIT_RESET_SCORE
                reset_to_50.append(p.id)
            elif p.score > ELIMINATION_SCORE and not p.eliminated:
                p.eliminated = True
                newly_eliminated.append(p.id)

        self.last_round_summary = {
            "caller_id": caller_id,
            "caller_won": caller_wins,
            "empty_hand_ids": empty_hand_ids,
            "hand_sums": sums,
            "score_deltas": deltas,
            "newly_eliminated": newly_eliminated,
            "reset_to_50": reset_to_50,
        }

        self.drawn_card = None
        self.drawn_card_owner = None
        self.pending_power = None
        self.pending_power_owner = None

        remaining = self._active_players()
        if len(remaining) <= 1:
            self.phase = Phase.GAME_OVER
            self.winner_id = remaining[0].id if remaining else None
        else:
            self.phase = Phase.ROUND_OVER

    # ------------------------------------------------------------------
    # Serialization (per-viewer visibility rules)
    # ------------------------------------------------------------------

    def public_state(self, viewer_id: str) -> dict:
        self._purge_expired_reveals()
        now = self._clock()
        # Once a round ends (GABO called or the game is over), every hand is
        # shown face up to everyone, exactly as it would be on a real table.
        reveal_all = self.phase in (Phase.ROUND_OVER, Phase.GAME_OVER)

        def hand_view(owner: Player) -> list[dict]:
            slots = []
            for idx, card in enumerate(owner.hand):
                if reveal_all:
                    slots.append({"hidden": False, "card": card.to_dict()})
                    continue
                reveal = next(
                    (r for r in self._reveals
                     if r.viewer_id == viewer_id and r.target_player_id == owner.id and r.hand_index == idx),
                    None,
                )
                if reveal is not None:
                    slots.append({"hidden": False, "card": reveal.card.to_dict(),
                                  "expires_in": max(0.0, reveal.expires_at - now)})
                else:
                    slots.append({"hidden": True})
            return slots

        players_view = []
        for p in self.players:
            players_view.append({
                "id": p.id,
                "name": p.name,
                "score": p.score,
                "eliminated": p.eliminated,
                "hand_size": len(p.hand),
                "hand": hand_view(p),
            })

        return {
            "phase": self.phase.value,
            "round_number": self.round_number,
            "current_player_id": self.players[self.current_index].id if self.players else None,
            "gabo_caller_id": self.gabo_caller_id,
            "draw_pile_count": len(self.deck.draw_pile),
            "top_discard": self.deck.top_discard.to_dict() if self.deck.top_discard else None,
            "drawn_card": (
                self.drawn_card.to_dict()
                if self.drawn_card is not None and self.drawn_card_owner == viewer_id
                else (None if self.drawn_card is None else {"hidden": True})
            ),
            "pending_power": self.pending_power.value if self.pending_power else None,
            "pending_power_owner": self.pending_power_owner,
            "initial_peek_deadline": self.initial_peek_deadline,
            "players_peeked": sorted(self._peeked_players),
            "players": players_view,
            "winner_id": self.winner_id,
            "last_round_summary": self.last_round_summary,
            "doubles_window_remaining": max(0.0, self.doubles_window_until - now),
            "final_doubles_remaining": (
                max(0.0, self.final_doubles_deadline - now) if self.final_doubles_deadline is not None else None
            ),
            "actions": list(self.actions),
        }
