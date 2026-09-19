import pytest

from pricetime.commands import CancelOrder
from pricetime.engine import MatchingEngine
from pricetime.events import CancelReason, CancelRejected, OrderCancelled, RejectReason
from pricetime.orders import Side
from tests.support import buy, market_buy, resting, sell


def test_cancel_removes_the_resting_order_and_reports_its_open_quantity() -> None:
    engine = MatchingEngine()
    engine.process(buy(100, 10))

    events = engine.process(CancelOrder(order_id=1))

    assert events == [OrderCancelled(order_id=1, quantity=10, reason=CancelReason.REQUESTED)]
    assert resting(engine, Side.BUY) == []
    assert 1 not in engine.book


def test_cancel_leaves_the_rest_of_the_level_in_order() -> None:
    engine = MatchingEngine()
    for _ in range(3):
        engine.process(sell(100, 5))

    engine.process(CancelOrder(order_id=2))

    assert resting(engine, Side.SELL) == [(100, 1, 5), (100, 3, 5)]


@pytest.mark.parametrize("order_id", [2, 99, 0, -1])
def test_cancel_for_an_id_never_issued_is_rejected_as_unknown(order_id: int) -> None:
    engine = MatchingEngine()
    engine.process(buy(100, 10))

    events = engine.process(CancelOrder(order_id=order_id))

    assert events == [CancelRejected(order_id=order_id, reason=RejectReason.UNKNOWN_ORDER)]
    assert resting(engine, Side.BUY) == [(100, 1, 10)]


def test_cancel_after_the_order_fully_filled_is_too_late_and_the_trade_stands() -> None:
    engine = MatchingEngine()
    engine.process(sell(100, 10))
    engine.process(buy(100, 10))

    events = engine.process(CancelOrder(order_id=1))

    assert events == [CancelRejected(order_id=1, reason=RejectReason.TOO_LATE)]
    assert len(engine.book) == 0


def test_cancel_after_a_partial_fill_removes_only_the_unfilled_part() -> None:
    engine = MatchingEngine()
    engine.process(sell(100, 10))
    engine.process(buy(100, 4))

    events = engine.process(CancelOrder(order_id=1))

    assert events == [OrderCancelled(order_id=1, quantity=6, reason=CancelReason.REQUESTED)]
    assert len(engine.book) == 0


def test_cancelling_the_same_order_twice_rejects_the_second_as_too_late() -> None:
    engine = MatchingEngine()
    engine.process(buy(100, 10))
    engine.process(CancelOrder(order_id=1))

    events = engine.process(CancelOrder(order_id=1))

    assert events == [CancelRejected(order_id=1, reason=RejectReason.TOO_LATE)]


def test_cancel_for_a_market_order_is_too_late_because_it_never_rests() -> None:
    engine = MatchingEngine()
    engine.process(sell(100, 10))
    engine.process(market_buy(4))

    events = engine.process(CancelOrder(order_id=2))

    assert events == [CancelRejected(order_id=2, reason=RejectReason.TOO_LATE)]
    assert resting(engine, Side.SELL) == [(100, 1, 6)]


def test_cancelling_the_last_order_at_the_best_price_moves_the_best_price() -> None:
    engine = MatchingEngine()
    engine.process(sell(100, 5))
    engine.process(sell(101, 5))

    engine.process(CancelOrder(order_id=1))

    assert engine.book.best_ask() == 101
