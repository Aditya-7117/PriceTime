import pytest

from pricetime.events import OrderAccepted, OrderRejected, RejectReason, Trade
from pricetime.orders import Side
from tests.support import banded, buy, new_engine, resting, sell


def test_order_that_does_not_cross_rests_on_the_book() -> None:
    engine = new_engine()

    events = engine.process(buy(100, 10))

    assert events == [OrderAccepted(order_id=1, side=Side.BUY, price=100, quantity=10)]
    assert resting(engine, Side.BUY) == [(100, 1, 10)]
    assert engine.book.best_bid() == 100


def test_crossing_order_trades_at_the_resting_price() -> None:
    engine = new_engine()
    engine.process(sell(100, 10))

    events = engine.process(buy(102, 10))

    assert events == [
        OrderAccepted(order_id=2, side=Side.BUY, price=102, quantity=10),
        Trade(
            trade_id=1,
            price=100,
            quantity=10,
            maker_order_id=1,
            taker_order_id=2,
            aggressor=Side.BUY,
        ),
    ]
    assert resting(engine, Side.BUY) == []
    assert resting(engine, Side.SELL) == []


def test_unfilled_part_of_an_aggressive_order_rests_at_its_limit() -> None:
    engine = new_engine()
    engine.process(sell(100, 5))

    engine.process(buy(101, 8))

    assert resting(engine, Side.BUY) == [(101, 2, 3)]
    assert resting(engine, Side.SELL) == []
    (order,) = engine.snapshot().bids
    assert order.filled == 5


def test_partially_filled_resting_order_keeps_its_place_at_the_front() -> None:
    engine = new_engine()
    engine.process(sell(100, 10))
    engine.process(sell(100, 10))

    engine.process(buy(100, 4))

    assert resting(engine, Side.SELL) == [(100, 1, 6), (100, 2, 10)]


def test_order_sweeps_several_levels_and_rests_the_remainder() -> None:
    engine = new_engine()
    engine.process(sell(101, 100))
    engine.process(sell(102, 100))
    engine.process(sell(103, 200))
    engine.process(sell(106, 300))

    events = engine.process(buy(105, 500))

    trades = [(e.maker_order_id, e.price, e.quantity) for e in events if isinstance(e, Trade)]
    assert trades == [(1, 101, 100), (2, 102, 100), (3, 103, 200)]
    assert resting(engine, Side.BUY) == [(105, 5, 100)]
    assert resting(engine, Side.SELL) == [(106, 4, 300)]
    assert engine.book.best_bid() == 105
    assert engine.book.best_ask() == 106


def test_sell_sweeps_bids_from_the_highest_price_down() -> None:
    engine = new_engine()
    engine.process(buy(99, 10))
    engine.process(buy(100, 10))

    events = engine.process(sell(99, 15))

    trades = [(e.maker_order_id, e.price, e.quantity) for e in events if isinstance(e, Trade)]
    assert trades == [(2, 100, 10), (1, 99, 5)]
    assert all(e.aggressor is Side.SELL for e in events if isinstance(e, Trade))
    assert resting(engine, Side.BUY) == [(99, 1, 5)]


def test_earlier_order_at_the_same_price_fills_first() -> None:
    engine = new_engine()
    for _ in range(3):
        engine.process(sell(100, 5))

    events = engine.process(buy(100, 12))

    trades = [(e.maker_order_id, e.quantity) for e in events if isinstance(e, Trade)]
    assert trades == [(1, 5), (2, 5), (3, 2)]
    assert resting(engine, Side.SELL) == [(100, 3, 3)]


def test_better_price_fills_before_earlier_time() -> None:
    engine = new_engine()
    engine.process(sell(101, 5))
    engine.process(sell(100, 5))

    events = engine.process(buy(101, 5))

    trades = [(e.maker_order_id, e.price) for e in events if isinstance(e, Trade)]
    assert trades == [(2, 100)]


def test_limit_order_never_trades_through_its_limit() -> None:
    engine = new_engine()
    engine.process(sell(101, 5))

    events = engine.process(buy(100, 5))

    assert not any(isinstance(e, Trade) for e in events)
    assert resting(engine, Side.BUY) == [(100, 2, 5)]
    assert resting(engine, Side.SELL) == [(101, 1, 5)]


@pytest.mark.parametrize("quantity", [0, -5])
def test_non_positive_quantity_is_rejected(quantity: int) -> None:
    engine = new_engine()

    events = engine.process(buy(100, quantity))

    assert events == [OrderRejected(order_id=1, reason=RejectReason.INVALID_QUANTITY)]
    assert len(engine.book) == 0


@pytest.mark.parametrize("price", [0, -1])
def test_non_positive_price_is_rejected(price: int) -> None:
    engine = new_engine()

    events = engine.process(sell(price, 10))

    assert events == [OrderRejected(order_id=1, reason=RejectReason.INVALID_PRICE)]
    assert len(engine.book) == 0


def test_every_new_order_gets_the_next_id_even_when_rejected() -> None:
    engine = new_engine()

    first = engine.process(buy(100, 0))
    second = engine.process(buy(100, 10))

    assert first == [OrderRejected(order_id=1, reason=RejectReason.INVALID_QUANTITY)]
    assert second == [OrderAccepted(order_id=2, side=Side.BUY, price=100, quantity=10)]


def test_trade_ids_count_up_across_orders() -> None:
    engine = new_engine()
    engine.process(sell(100, 5))
    engine.process(sell(101, 5))

    first = engine.process(buy(100, 5))
    second = engine.process(buy(101, 5))

    trade_ids = [e.trade_id for e in first + second if isinstance(e, Trade)]
    assert trade_ids == [1, 2]


@pytest.mark.parametrize("price", [89, 111])
def test_order_priced_outside_the_daily_band_is_rejected(price: int) -> None:
    engine = new_engine(banded(90, 110))

    events = engine.process(buy(price, 10))

    assert events == [OrderRejected(order_id=1, reason=RejectReason.PRICE_OUT_OF_BAND)]
    assert len(engine.book) == 0


@pytest.mark.parametrize("price", [90, 110])
def test_orders_at_the_band_limits_are_accepted(price: int) -> None:
    engine = new_engine(banded(90, 110))

    events = engine.process(sell(price, 10))

    assert events == [OrderAccepted(order_id=1, side=Side.SELL, price=price, quantity=10)]
