import pytest

from pricetime.events import (
    CancelReason,
    OrderAccepted,
    OrderCancelled,
    OrderRejected,
    RejectReason,
    Trade,
)
from pricetime.orders import Side
from tests.support import buy, market_buy, market_sell, new_engine, resting, sell


def test_market_order_sweeps_the_book_and_cancels_what_it_cannot_fill() -> None:
    engine = new_engine()
    engine.process(sell(100, 5))
    engine.process(sell(101, 5))

    events = engine.process(market_buy(12))

    assert events == [
        OrderAccepted(order_id=3, side=Side.BUY, price=None, quantity=12),
        Trade(
            trade_id=1,
            price=100,
            quantity=5,
            maker_order_id=1,
            taker_order_id=3,
            aggressor=Side.BUY,
        ),
        Trade(
            trade_id=2,
            price=101,
            quantity=5,
            maker_order_id=2,
            taker_order_id=3,
            aggressor=Side.BUY,
        ),
        OrderCancelled(order_id=3, quantity=2, reason=CancelReason.NO_LIQUIDITY),
    ]
    assert resting(engine, Side.SELL) == []
    assert resting(engine, Side.BUY) == []


def test_market_order_into_an_empty_book_is_cancelled_in_full() -> None:
    engine = new_engine()

    events = engine.process(market_sell(7))

    assert events == [
        OrderAccepted(order_id=1, side=Side.SELL, price=None, quantity=7),
        OrderCancelled(order_id=1, quantity=7, reason=CancelReason.NO_LIQUIDITY),
    ]
    assert len(engine.book) == 0


def test_filled_market_order_leaves_no_cancel_and_never_rests() -> None:
    engine = new_engine()
    engine.process(buy(100, 10))

    events = engine.process(market_sell(4))

    assert not any(isinstance(e, OrderCancelled) for e in events)
    assert resting(engine, Side.BUY) == [(100, 1, 6)]
    assert resting(engine, Side.SELL) == []


def test_market_order_trades_at_any_price_on_the_book() -> None:
    engine = new_engine()
    engine.process(buy(1, 5))

    events = engine.process(market_sell(5))

    trades = [(e.price, e.quantity) for e in events if isinstance(e, Trade)]
    assert trades == [(1, 5)]


@pytest.mark.parametrize("quantity", [0, -1])
def test_market_order_with_non_positive_quantity_is_rejected(quantity: int) -> None:
    engine = new_engine()
    engine.process(sell(100, 5))

    events = engine.process(market_buy(quantity))

    assert events == [OrderRejected(order_id=2, reason=RejectReason.INVALID_QUANTITY)]
    assert resting(engine, Side.SELL) == [(100, 1, 5)]
