"""Invariants that hold after every command of any command sequence."""

from hypothesis import given

from pricetime.commands import CancelOrder, Command, ModifyOrder
from pricetime.engine import MatchingEngine
from pricetime.events import CancelRejected, OrderCancelled
from tests.invariants import (
    Ledger,
    check_modify_priority,
    check_not_crossed,
    check_price_time_priority,
    check_structure,
    resting_orders,
)
from tests.strategies import command_sequences


@given(command_sequences())
def test_book_never_crosses(commands: list[Command]) -> None:
    engine = MatchingEngine()
    for command in commands:
        engine.process(command)
        check_not_crossed(engine)


@given(command_sequences())
def test_shares_are_conserved(commands: list[Command]) -> None:
    engine = MatchingEngine()
    ledger = Ledger()
    for command in commands:
        ledger.record(command, engine.process(command))
        ledger.check(engine)


@given(command_sequences())
def test_cancel_removes_exactly_one_order(commands: list[Command]) -> None:
    engine = MatchingEngine()
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


@given(command_sequences())
def test_replay_is_deterministic(commands: list[Command]) -> None:
    first, second = MatchingEngine(), MatchingEngine()

    first_events = [first.process(command) for command in commands]
    second_events = [second.process(command) for command in commands]

    assert first_events == second_events
    assert first.snapshot() == second.snapshot()


@given(command_sequences())
def test_trades_follow_price_time_priority(commands: list[Command]) -> None:
    engine = MatchingEngine()
    for command in commands:
        before = engine.snapshot()
        events = engine.process(command)
        check_price_time_priority(before, command, events)


@given(command_sequences())
def test_modify_keeps_queue_position_only_when_shrinking_in_place(commands: list[Command]) -> None:
    engine = MatchingEngine()
    for command in commands:
        before = engine.snapshot()
        events = engine.process(command)
        if isinstance(command, ModifyOrder):
            check_modify_priority(before, engine.snapshot(), command, events)


@given(command_sequences())
def test_book_structure_stays_consistent(commands: list[Command]) -> None:
    engine = MatchingEngine()
    for command in commands:
        engine.process(command)
        check_structure(engine)
