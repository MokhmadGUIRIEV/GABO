import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from app.game.cards import Card, Rank, Suit, ELIMINATION_SCORE, GABO_PENALTY
from app.game.engine import GameEngine, GameError, Phase, PowerType


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def advance(self, dt: float) -> None:
        self.t += dt


def make_engine(n_players=3, seed=1, clock=None):
    ids = [f"p{i}" for i in range(n_players)]
    names = {pid: pid.upper() for pid in ids}
    return GameEngine(ids, names, rng_seed=seed, clock=clock or FakeClock())


# ---------------------------------------------------------------------------
# Card values
# ---------------------------------------------------------------------------

def test_card_values():
    assert Card(Rank.ACE, Suit.HEART).value == 1
    assert Card(Rank.TEN, Suit.CLUB).value == 10
    assert Card(Rank.JACK, Suit.SPADE).value == 11
    assert Card(Rank.QUEEN, Suit.DIAMOND).value == 12
    assert Card(Rank.KING, Suit.HEART).value == 35
    assert Card(Rank.KING, Suit.DIAMOND).value == 35
    assert Card(Rank.KING, Suit.SPADE).value == 0
    assert Card(Rank.KING, Suit.CLUB).value == 0


def test_power_flags():
    assert Card(Rank.SEVEN, Suit.HEART).grants_peek_own
    assert Card(Rank.EIGHT, Suit.HEART).grants_peek_own
    assert Card(Rank.NINE, Suit.HEART).grants_peek_opponent
    assert Card(Rank.TEN, Suit.HEART).grants_peek_opponent
    assert Card(Rank.JACK, Suit.HEART).grants_swap_and_peek
    assert Card(Rank.QUEEN, Suit.HEART).grants_swap_and_peek
    assert not Card(Rank.SIX, Suit.HEART).grants_peek_own


# ---------------------------------------------------------------------------
# Setup / dealing
# ---------------------------------------------------------------------------

def test_start_round_deals_four_cards_each_and_enters_initial_peek():
    engine = make_engine(3)
    engine.start_round()
    assert engine.phase == Phase.INITIAL_PEEK
    for p in engine.players:
        assert len(p.hand) == 4
    assert engine.round_number == 1


def test_engine_rejects_invalid_player_counts():
    with pytest.raises(GameError):
        GameEngine(["a"], {"a": "A"})
    with pytest.raises(GameError):
        GameEngine([f"p{i}" for i in range(7)], {})


# ---------------------------------------------------------------------------
# Initial peek visibility
# ---------------------------------------------------------------------------

def test_initial_peek_reveals_only_to_owner_and_expires():
    clock = FakeClock()
    engine = make_engine(2, clock=clock)
    engine.start_round()
    p0, p1 = engine.players
    engine.peek_initial(p0.id, [0, 1])

    view0 = engine.public_state(p0.id)
    view1 = engine.public_state(p1.id)
    assert view0["players"][0]["hand"][0]["hidden"] is False
    assert view0["players"][0]["hand"][2]["hidden"] is True
    # Player 1 must never see player 0's revealed cards.
    assert view1["players"][0]["hand"][0]["hidden"] is True

    with pytest.raises(GameError):
        engine.peek_initial(p0.id, [2, 3])  # already peeked

    clock.advance(6)  # past the 5s window
    view0_later = engine.public_state(p0.id)
    assert view0_later["players"][0]["hand"][0]["hidden"] is True


def test_peek_initial_requires_exactly_two_distinct_indices():
    engine = make_engine(2)
    engine.start_round()
    p0 = engine.players[0]
    with pytest.raises(GameError):
        engine.peek_initial(p0.id, [0])
    with pytest.raises(GameError):
        engine.peek_initial(p0.id, [0, 0])


# ---------------------------------------------------------------------------
# Turn flow: draw / discard / swap
# ---------------------------------------------------------------------------

def test_draw_then_discard_non_power_card_ends_turn():
    engine = make_engine(3)
    engine.start_round()
    engine.finish_initial_peek()
    starter = engine.current_player
    # Force a harmless drawn card (a 2, no power).
    engine.deck.draw_pile.append(Card(Rank.TWO, Suit.HEART))
    engine.draw_card(starter.id)
    power = engine.discard_drawn(starter.id)
    assert power is None
    assert engine.phase == Phase.TURN
    assert engine.current_player.id != starter.id


def test_swap_drawn_card_cancels_power_even_for_special_rank():
    engine = make_engine(2)
    engine.start_round()
    engine.finish_initial_peek()
    starter = engine.current_player
    engine.deck.draw_pile.append(Card(Rank.NINE, Suit.SPADE))
    engine.draw_card(starter.id)
    old_card = engine.swap_drawn(starter.id, 0)
    assert starter.hand[0] == Card(Rank.NINE, Suit.SPADE)
    assert engine.deck.top_discard == old_card
    # No power pending, power was cancelled by swapping.
    assert engine.phase == Phase.TURN
    assert engine.pending_power is None


def test_cannot_draw_when_not_your_turn():
    engine = make_engine(3)
    engine.start_round()
    engine.finish_initial_peek()
    not_current = engine.players[(engine.current_index + 1) % 3]
    with pytest.raises(GameError):
        engine.draw_card(not_current.id)


# ---------------------------------------------------------------------------
# Powers
# ---------------------------------------------------------------------------

def test_power_peek_own_reveals_card_to_self_only():
    clock = FakeClock()
    engine = make_engine(2, clock=clock)
    engine.start_round()
    engine.finish_initial_peek()
    starter = engine.current_player
    other = engine.players[1] if engine.players[0].id == starter.id else engine.players[0]

    engine.deck.draw_pile.append(Card(Rank.SEVEN, Suit.HEART))
    engine.draw_card(starter.id)
    power = engine.discard_drawn(starter.id)
    assert power == PowerType.PEEK_OWN
    assert engine.phase == Phase.POWER_PENDING

    revealed = engine.use_power_peek_own(starter.id, 0)
    assert revealed == starter.hand[0]
    view_self = engine.public_state(starter.id)
    view_other = engine.public_state(other.id)
    assert view_self["players"][engine.players.index(starter)]["hand"][0]["hidden"] is False
    assert view_other["players"][engine.players.index(starter)]["hand"][0]["hidden"] is True
    assert engine.phase == Phase.TURN


def test_power_peek_opponent_must_target_someone_else():
    engine = make_engine(2)
    engine.start_round()
    engine.finish_initial_peek()
    starter = engine.current_player
    engine.deck.draw_pile.append(Card(Rank.NINE, Suit.CLUB))
    engine.draw_card(starter.id)
    engine.discard_drawn(starter.id)
    with pytest.raises(GameError):
        engine.use_power_peek_opponent(starter.id, starter.id, 0)


def test_power_swap_and_peek_swaps_cards_and_reveals_new_one():
    engine = make_engine(2)
    engine.start_round()
    engine.finish_initial_peek()
    starter = engine.current_player
    opponent = engine.players[1] if engine.players[0].id == starter.id else engine.players[0]
    own_before = starter.hand[0]
    opp_before = opponent.hand[0]

    engine.deck.draw_pile.append(Card(Rank.JACK, Suit.DIAMOND))
    engine.draw_card(starter.id)
    engine.discard_drawn(starter.id)
    new_card = engine.use_power_swap_and_peek(starter.id, 0, opponent.id, 0)

    assert starter.hand[0] == opp_before
    assert opponent.hand[0] == own_before
    assert new_card == opp_before
    assert engine.phase == Phase.TURN


def test_skip_power_ends_turn_without_effect():
    engine = make_engine(2)
    engine.start_round()
    engine.finish_initial_peek()
    starter = engine.current_player
    hand_before = list(starter.hand)
    engine.deck.draw_pile.append(Card(Rank.EIGHT, Suit.SPADE))
    engine.draw_card(starter.id)
    engine.discard_drawn(starter.id)
    engine.skip_power(starter.id)
    assert starter.hand == hand_before
    assert engine.phase == Phase.TURN
    assert engine.current_player.id != starter.id


# ---------------------------------------------------------------------------
# Snap
# ---------------------------------------------------------------------------

def test_snap_success_removes_card_from_hand():
    engine = make_engine(2)
    engine.start_round()
    engine.finish_initial_peek()
    starter = engine.current_player
    other = engine.players[1] if engine.players[0].id == starter.id else engine.players[0]
    other.hand[2] = Card(Rank.EIGHT, Suit.CLUB)

    engine.deck.draw_pile.append(Card(Rank.EIGHT, Suit.HEART))
    engine.draw_card(starter.id)
    engine.discard_drawn(starter.id)  # triggers power_pending, but discard pile top is set immediately
    assert engine.deck.top_discard.rank == Rank.EIGHT

    size_before = len(other.hand)
    result = engine.snap_attempt(other.id, 2)
    assert result is True
    assert len(other.hand) == size_before - 1
    assert engine.deck.top_discard.rank == Rank.EIGHT


def test_snap_failure_gives_penalty_card():
    engine = make_engine(2)
    engine.start_round()
    engine.finish_initial_peek()
    starter = engine.current_player
    other = engine.players[1] if engine.players[0].id == starter.id else engine.players[0]
    other.hand[2] = Card(Rank.THREE, Suit.CLUB)

    engine.deck.draw_pile.append(Card(Rank.EIGHT, Suit.HEART))
    engine.draw_card(starter.id)
    engine.discard_drawn(starter.id)

    size_before = len(other.hand)
    result = engine.snap_attempt(other.id, 2)
    assert result is False
    assert len(other.hand) == size_before + 1  # wrong card kept + penalty card added


# ---------------------------------------------------------------------------
# GABO call and scoring
# ---------------------------------------------------------------------------

def test_call_gabo_only_valid_at_start_of_current_players_turn():
    engine = make_engine(3)
    engine.start_round()
    engine.finish_initial_peek()
    not_current = engine.players[(engine.current_index + 1) % 3]
    with pytest.raises(GameError):
        engine.call_gabo(not_current.id)


def test_gabo_caller_strictly_lowest_wins_round():
    engine = make_engine(3)
    engine.start_round()
    engine.finish_initial_peek()

    caller, p1, p2 = engine.players
    engine.current_index = engine.players.index(caller)
    caller.hand = [Card(Rank.ACE, Suit.HEART), Card(Rank.TWO, Suit.CLUB)]  # sum 3
    p1.hand = [Card(Rank.ACE, Suit.SPADE), Card(Rank.FOUR, Suit.CLUB)]  # sum 5
    p2.hand = [Card(Rank.SEVEN, Suit.HEART), Card(Rank.JACK, Suit.CLUB)]  # sum 18

    engine.call_gabo(caller.id)

    assert engine.phase == Phase.ROUND_OVER
    assert caller.score == 0
    assert p1.score == 5
    assert p2.score == 18
    summary = engine.last_round_summary
    assert summary["caller_won"] is True


def test_gabo_caller_tied_or_beaten_gets_penalty_only():
    engine = make_engine(3)
    engine.start_round()
    engine.finish_initial_peek()

    caller, p1, p2 = engine.players
    engine.current_index = engine.players.index(caller)
    caller.hand = [Card(Rank.FIVE, Suit.HEART)]  # sum 5
    p1.hand = [Card(Rank.TWO, Suit.CLUB), Card(Rank.THREE, Suit.CLUB)]  # sum 5 (tie beats caller)
    p2.hand = [Card(Rank.NINE, Suit.HEART)]  # sum 9

    engine.call_gabo(caller.id)

    assert caller.score == GABO_PENALTY
    assert p1.score == 0
    assert p2.score == 0
    assert engine.last_round_summary["caller_won"] is False


def test_elimination_at_100_and_game_over_with_one_player_left():
    engine = make_engine(2)
    engine.start_round()
    engine.finish_initial_peek()

    caller, opponent = engine.players
    engine.current_index = engine.players.index(caller)
    caller.score = 70
    caller.hand = [Card(Rank.KING, Suit.HEART)]  # loses badly if wrong, but let's make caller win big
    opponent.hand = [Card(Rank.ACE, Suit.HEART)]

    # Caller has a King (35, worse than opponent's Ace) -> caller should lose the call.
    engine.call_gabo(caller.id)
    assert caller.score == 70 + GABO_PENALTY  # 105 -> eliminated
    assert caller.eliminated is True
    assert engine.phase == Phase.GAME_OVER
    assert engine.winner_id == opponent.id


def test_second_round_starts_next_to_previous_starter_and_skips_eliminated():
    engine = make_engine(3)
    engine.start_round()
    engine.finish_initial_peek()
    first_starter_idx = engine._starting_index

    caller = engine.players[first_starter_idx]
    others = [p for p in engine.players if p.id != caller.id]
    engine.current_index = first_starter_idx
    caller.hand = [Card(Rank.ACE, Suit.HEART)]
    others[0].hand = [Card(Rank.TWO, Suit.HEART)]
    others[1].hand = [Card(Rank.THREE, Suit.HEART)]
    engine.call_gabo(caller.id)
    assert engine.phase == Phase.ROUND_OVER

    engine.start_round()
    assert engine.phase == Phase.INITIAL_PEEK
    assert engine.round_number == 2
    expected_next = (first_starter_idx + 1) % 3
    assert engine._starting_index == expected_next
    for p in engine.players:
        assert len(p.hand) == 4
