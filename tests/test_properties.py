"""Invariants that hold after every command of any command sequence."""

from hypothesis import given

from pricetime.commands import CancelOrder, Command, ModifyOrder
from pricetime.events import CancelRejected, OrderCancelled
from pricetime.rules import MarketRules
from tests.invariants import (
    Ledger,
    check_modify_priority,
    check_not_crossed,
    check_price_time_priority,
    check_structure,
    check_within_band,
    resting_orders,
)
from tests.strategies import command_sequences, market_rules
from tests.support import new_engine


@given(market_rules, command_sequences())
def test_book_never_crosses(rules: MarketRules, commands: list[Command]) -> None:
    engine = new_engine(rules)
    for command in commands:
        engine.process(command)
        check_not_crossed(engine)


@given(market_rules, command_sequences())
def test_shares_are_conserved(rules: MarketRules, commands: list[Command]) -> None:
    engine = new_engine(rules)
    ledger = Ledger()
    for command in commands:
        ledger.record(command, engine.process(command))
        ledger.check(engine)


@given(market_rules, command_sequences())
def test_cancel_removes_exactly_one_order(rules: MarketRules, commands: list[Command]) -> None:
    engine = new_engine(rules)
    for command in commands:
        before = resting_orders(engine.snapshot())
        events = engine.process(command)
        if not isinstance(command, CancelOrder):
            continue
        after = resting_orders(engine.snapshot())
        match events:
            case [OrderCancelled(order_id=order_id)]:
                assert order_id == command.order_id
                assert after == tuple(o for o in before if o.order_id != order_id)
                assert len(after) == len(before) - 1
            case [CancelRejected()]:
                assert after == before
            case _:
                raise AssertionError(f"unexpected cancel outcome: {events}")


@given(market_rules, command_sequences())
def test_replay_is_deterministic(rules: MarketRules, commands: list[Command]) -> None:
    first, second = new_engine(rules), new_engine(rules)

    first_events = [first.process(command) for command in commands]
    second_events = [second.process(command) for command in commands]

    assert first_events == second_events
    assert first.snapshot() == second.snapshot()


@given(market_rules, command_sequences())
def test_trades_follow_price_time_priority(rules: MarketRules, commands: list[Command]) -> None:
    engine = new_engine(rules)
    for command in commands:
        before = engine.snapshot()
        events = engine.process(command)
        check_price_time_priority(before, command, events)


@given(market_rules, command_sequences())
def test_modify_keeps_queue_position_only_when_shrinking_in_place(
    rules: MarketRules, commands: list[Command]
) -> None:
    engine = new_engine(rules)
    for command in commands:
        before = engine.snapshot()
        events = engine.process(command)
        if isinstance(command, ModifyOrder):
            check_modify_priority(before, engine.snapshot(), command, events)


@given(market_rules, command_sequences())
def test_book_structure_stays_consistent(rules: MarketRules, commands: list[Command]) -> None:
    engine = new_engine(rules)
    for command in commands:
        engine.process(command)
        check_structure(engine)


@given(market_rules, command_sequences())
def test_no_order_ever_rests_outside_the_price_band(
    rules: MarketRules, commands: list[Command]
) -> None:
    engine = new_engine(rules)
    for command in commands:
        engine.process(command)
        check_within_band(engine, rules)
