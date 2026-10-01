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


def make_engine(n_players=3, seed=1, clock=None, doubles_window=0):
    # No doubles window by default: most tests chain actions instantly.
    ids = [f"p{i}" for i in range(n_players)]
    names = {pid: pid.upper() for pid in ids}
    return GameEngine(ids, names, rng_seed=seed, clock=clock or FakeClock(),
                      doubles_window_seconds=doubles_window)


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


def test_finish_initial_peek_flips_a_starting_discard_card():
    engine = make_engine(3)
    engine.start_round()
    assert engine.deck.top_discard is None
    draw_count_before = len(engine.deck.draw_pile)
    engine.finish_initial_peek()
    assert engine.deck.top_discard is not None
    assert len(engine.deck.draw_pile) == draw_count_before - 1
    # Snapping must already be possible before anyone has taken a turn.
    snapper = engine.players[1]
    snapper.hand[0] = engine.deck.top_discard
    assert engine.snap_attempt(snapper.id, 0) is True


def test_finish_initial_peek_does_not_flip_twice_across_calls():
    engine = make_engine(2)
    engine.start_round()
    engine.finish_initial_peek()
    top = engine.deck.top_discard
    # A later call within the same round (e.g. a defensive re-invocation)
    # must not burn another card once a discard top already exists.
    engine.phase = Phase.INITIAL_PEEK
    engine.finish_initial_peek()
    assert engine.deck.top_discard == top


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


def test_round_over_reveals_every_hand_to_every_viewer():
    engine = make_engine(3)
    engine.start_round()
    engine.finish_initial_peek()

    caller, p1, p2 = engine.players
    engine.current_index = engine.players.index(caller)
    caller.hand = [Card(Rank.ACE, Suit.HEART), Card(Rank.TWO, Suit.CLUB)]
    p1.hand = [Card(Rank.ACE, Suit.SPADE), Card(Rank.FOUR, Suit.CLUB)]
    p2.hand = [Card(Rank.SEVEN, Suit.HEART), Card(Rank.JACK, Suit.CLUB)]

    engine.call_gabo(caller.id)
    assert engine.phase == Phase.ROUND_OVER

    for viewer in ["__public__", caller.id, p1.id, p2.id]:
        state = engine.public_state(viewer)
        for player_view, player in zip(state["players"], engine.players):
            for slot, card in zip(player_view["hand"], player.hand):
                assert slot["hidden"] is False
                assert slot["card"] == card.to_dict()


def test_game_over_also_reveals_every_hand():
    engine = make_engine(2)
    engine.start_round()
    engine.finish_initial_peek()

    caller, opponent = engine.players
    engine.current_index = engine.players.index(caller)
    caller.score = 70
    caller.hand = [Card(Rank.KING, Suit.HEART)]
    opponent.hand = [Card(Rank.ACE, Suit.HEART)]

    engine.call_gabo(caller.id)
    assert engine.phase == Phase.GAME_OVER

    state = engine.public_state("__public__")
    caller_view = next(p for p in state["players"] if p["id"] == caller.id)
    assert caller_view["hand"][0]["hidden"] is False
    assert caller_view["hand"][0]["card"] == caller.hand[0].to_dict()


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


# ---------------------------------------------------------------------------
# Doubles window, empty hand, exact 100, action feed
# ---------------------------------------------------------------------------

def _ready_engine(n_players=3, window=3):
    clock = FakeClock()
    engine = make_engine(n_players, clock=clock, doubles_window=window)
    engine.start_round()
    engine.finish_initial_peek()
    return engine, clock


def test_doubles_window_after_first_discard_and_after_each_turn():
    engine, clock = _ready_engine()
    current = engine.current_player.id
    # The first discard card was just flipped: nobody plays for 3 seconds.
    with pytest.raises(GameError):
        engine.draw_card(current)
    with pytest.raises(GameError):
        engine.call_gabo(current)
    assert engine.public_state(current)["doubles_window_remaining"] == pytest.approx(3)
    clock.advance(3)
    engine.draw_card(current)
    engine.drawn_card = Card(Rank.TWO, Suit.HEART)
    engine.discard_drawn(current)
    nxt = engine.current_player.id
    assert nxt != current
    with pytest.raises(GameError):
        engine.draw_card(nxt)
    clock.advance(2.9)
    with pytest.raises(GameError):
        engine.draw_card(nxt)
    clock.advance(0.2)
    engine.draw_card(nxt)


def test_doubles_can_be_dropped_during_window_and_extend_it():
    engine, clock = _ready_engine()
    top = engine.deck.top_discard
    other = engine.players[(engine.current_index + 1) % 3]
    other.hand[0] = Card(top.rank, Suit.SPADE if top.suit != Suit.SPADE else Suit.CLUB)
    clock.advance(2)
    assert engine.snap_attempt(other.id, 0) is True
    # The pile changed: 3 more seconds from now.
    clock.advance(2)
    with pytest.raises(GameError):
        engine.draw_card(engine.current_player.id)
    clock.advance(1.1)
    engine.draw_card(engine.current_player.id)


def test_empty_hand_ends_round_after_last_doubles_window():
    engine, clock = _ready_engine()
    clock.advance(3)
    current = engine.current_player
    engine.draw_card(current.id)  # a turn is in progress when it happens
    drawn = engine.drawn_card
    pile_size = len(engine.deck.draw_pile)

    a, b = [p for p in engine.players if p.id != current.id]
    top = engine.deck.top_discard
    same = Card(top.rank, Suit.SPADE if top.suit != Suit.SPADE else Suit.CLUB)
    a.hand = [same]
    b.hand = [Card(top.rank, Suit.DIAMOND if top.suit != Suit.DIAMOND else Suit.HEART), Card(Rank.FIVE, Suit.CLUB)]
    current.hand = [Card(Rank.NINE, Suit.HEART), Card(Rank.TWO, Suit.CLUB)]

    assert engine.snap_attempt(a.id, 0) is True
    assert engine.phase == Phase.FINAL_DOUBLES
    # The interrupted turn is cancelled: the drawn card went back on the pile.
    assert engine.drawn_card is None and len(engine.deck.draw_pile) == pile_size + 1
    assert engine.deck.draw_pile[-1] == drawn
    with pytest.raises(GameError):
        engine.draw_card(current.id)
    assert engine.finish_final_doubles() is False

    # Someone else still drops their double in time.
    clock.advance(2)
    assert engine.snap_attempt(b.id, 0) is True
    clock.advance(2)
    assert engine.finish_final_doubles() is False  # window extended by that double
    clock.advance(1.1)
    assert engine.finish_final_doubles() is True

    assert engine.phase == Phase.ROUND_OVER
    summary = engine.last_round_summary
    assert summary["caller_id"] is None
    assert summary["empty_hand_ids"] == [a.id]
    assert summary["score_deltas"] == {a.id: 0, b.id: 5, current.id: 11}


def test_several_players_with_no_cards_all_score_zero():
    engine, clock = _ready_engine(window=0)
    a, b, c = engine.players
    top = engine.deck.top_discard
    a.hand = [Card(top.rank, Suit.SPADE if top.suit != Suit.SPADE else Suit.CLUB)]
    b.hand = [Card(top.rank, Suit.DIAMOND if top.suit != Suit.DIAMOND else Suit.HEART)]
    c.hand = [Card(Rank.THREE, Suit.CLUB)]
    engine.snap_attempt(a.id, 0)
    engine.snap_attempt(b.id, 0)
    assert engine.finish_final_doubles() is True
    assert engine.last_round_summary["score_deltas"] == {a.id: 0, b.id: 0, c.id: 3}


def test_exactly_100_goes_back_to_50_and_over_100_is_eliminated():
    engine, _ = _ready_engine(window=0)
    caller, lands_on_100, goes_over = engine.players
    engine.current_index = 0
    caller.hand = [Card(Rank.ACE, Suit.HEART)]
    lands_on_100.score, lands_on_100.hand = 90, [Card(Rank.TEN, Suit.HEART)]
    goes_over.score, goes_over.hand = 95, [Card(Rank.SIX, Suit.HEART)]
    engine.call_gabo(caller.id)
    assert lands_on_100.score == 50 and not lands_on_100.eliminated
    assert goes_over.score == 101 and goes_over.eliminated
    assert engine.last_round_summary["reset_to_50"] == [lands_on_100.id]
    assert engine.last_round_summary["newly_eliminated"] == [goes_over.id]


def test_actions_tell_everyone_which_cards_moved_and_from_where():
    engine, _ = _ready_engine(window=0)
    me = engine.current_player
    opp = engine.players[(engine.current_index + 1) % 3]
    engine.draw_card(me.id)
    engine.drawn_card = Card(Rank.QUEEN, Suit.HEART)
    engine.discard_drawn(me.id)
    engine.use_power_swap_and_peek(me.id, 2, opp.id, 1)
    nxt = engine.current_player
    engine.draw_card(nxt.id)
    engine.swap_drawn(nxt.id, 3)

    actions = engine.public_state(opp.id)["actions"]
    assert [a["type"] for a in actions] == ["draw", "discard_drawn", "power_swap", "draw", "swap_drawn"]
    swap = actions[2]
    assert swap["player_id"] == me.id and swap["power"] == "swap_and_peek"
    assert swap["power_card"] == {"rank": "dame", "suit": "coeur", "value": 12}
    assert (swap["own_index"], swap["target_id"], swap["target_index"]) == (2, opp.id, 1)
    assert actions[4]["hand_index"] == 3 and "discarded" in actions[4]
    assert [a["seq"] for a in actions] == sorted(a["seq"] for a in actions)
    # Never leaks a hidden card: only discarded (face-up) cards are named.
    assert "card" not in actions[0] and "card" not in swap


def test_first_doubles_window_starts_after_the_initial_peek():
    clock = FakeClock()
    engine = make_engine(2, clock=clock, doubles_window=3)
    engine.start_round()
    for p in engine.players:
        engine.peek_initial(p.id, [0, 1])
    clock.advance(1)
    engine.finish_initial_peek()  # everyone peeked: cards visible 4 more seconds
    current = engine.current_player.id
    clock.advance(4 + 2.9)
    with pytest.raises(GameError):
        engine.draw_card(current)
    clock.advance(0.2)
    engine.draw_card(current)
