"""Card definitions and deck for the GABO card game."""
from __future__ import annotations

import random
from dataclasses import dataclass
from enum import Enum


class Suit(str, Enum):
    HEART = "coeur"
    DIAMOND = "carreau"
    SPADE = "pique"
    CLUB = "trefle"


class Rank(str, Enum):
    ACE = "as"
    TWO = "2"
    THREE = "3"
    FOUR = "4"
    FIVE = "5"
    SIX = "6"
    SEVEN = "7"
    EIGHT = "8"
    NINE = "9"
    TEN = "10"
    JACK = "valet"
    QUEEN = "dame"
    KING = "roi"


# Point value of each rank. The King's value depends on its suit:
# King of Heart/Diamond = 35, King of Spade/Club = 0.
_RANK_VALUES = {
    Rank.ACE: 1,
    Rank.TWO: 2,
    Rank.THREE: 3,
    Rank.FOUR: 4,
    Rank.FIVE: 5,
    Rank.SIX: 6,
    Rank.SEVEN: 7,
    Rank.EIGHT: 8,
    Rank.NINE: 9,
    Rank.TEN: 10,
    Rank.JACK: 11,
    Rank.QUEEN: 12,
}

RED_KING_VALUE = 35
BLACK_KING_VALUE = 0

# Ranks that grant a power when drawn and discarded directly (not swapped in).
PEEK_OWN_RANKS = {Rank.SEVEN, Rank.EIGHT}
PEEK_OPPONENT_RANKS = {Rank.NINE, Rank.TEN}
SWAP_AND_PEEK_RANKS = {Rank.JACK, Rank.QUEEN}
POWER_RANKS = PEEK_OWN_RANKS | PEEK_OPPONENT_RANKS | SWAP_AND_PEEK_RANKS

GABO_PENALTY = 35
# Going past 100 eliminates the player; landing exactly on 100 sends the
# score back down to 50.
ELIMINATION_SCORE = 100
EXACT_LIMIT_RESET_SCORE = 50
INITIAL_HAND_SIZE = 4
INITIAL_PEEK_COUNT = 2
PEEK_DURATION_SECONDS = 5
# After a card lands on the discard pile, everyone gets this long to drop a
# matching card (a "doublon") before the next player may draw or call GABO.
# Same delay before a round ends because someone has no cards left.
DOUBLES_WINDOW_SECONDS = 3


@dataclass(frozen=True)
class Card:
    rank: Rank
    suit: Suit

    @property
    def value(self) -> int:
        if self.rank == Rank.KING:
            return RED_KING_VALUE if self.suit in (Suit.HEART, Suit.DIAMOND) else BLACK_KING_VALUE
        return _RANK_VALUES[self.rank]

    @property
    def grants_peek_own(self) -> bool:
        return self.rank in PEEK_OWN_RANKS

    @property
    def grants_peek_opponent(self) -> bool:
        return self.rank in PEEK_OPPONENT_RANKS

    @property
    def grants_swap_and_peek(self) -> bool:
        return self.rank in SWAP_AND_PEEK_RANKS

    def to_dict(self) -> dict:
        return {"rank": self.rank.value, "suit": self.suit.value, "value": self.value}

    def __str__(self) -> str:  # pragma: no cover - debug helper
        return f"{self.rank.value} de {self.suit.value}"


def build_deck() -> list[Card]:
    """Return the 52 cards of a standard deck used by GABO."""
    return [Card(rank, suit) for suit in Suit for rank in Rank]


class Deck:
    """A shuffled draw pile with a discard pile, supporting reshuffling."""

    def __init__(self, rng: random.Random | None = None):
        self._rng = rng or random.Random()
        self.draw_pile: list[Card] = build_deck()
        self.discard_pile: list[Card] = []
        self.shuffle()

    def shuffle(self) -> None:
        self._rng.shuffle(self.draw_pile)

    def draw(self) -> Card:
        if not self.draw_pile:
            self._reshuffle_discard_into_draw()
        return self.draw_pile.pop()

    def _reshuffle_discard_into_draw(self) -> None:
        if len(self.discard_pile) <= 1:
            raise RuntimeError("Plus de cartes disponibles pour la pioche.")
        top = self.discard_pile.pop()
        self.draw_pile = self.discard_pile
        self.discard_pile = [top]
        self.shuffle()

    def discard(self, card: Card) -> None:
        self.discard_pile.append(card)

    @property
    def top_discard(self) -> Card | None:
        return self.discard_pile[-1] if self.discard_pile else None

    def reset(self) -> None:
        self.draw_pile = build_deck()
        self.discard_pile = []
        self.shuffle()
