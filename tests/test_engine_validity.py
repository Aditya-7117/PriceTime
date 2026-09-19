from pricetime.commands import Validity
from pricetime.events import CancelReason, OrderAccepted, OrderCancelled, Trade
from pricetime.orders import Side
from tests.support import buy, new_engine, resting, sell


def test_ioc_order_trades_what_it_can_and_cancels_the_rest() -> None:
    engine = new_engine()
    engine.process(sell(100, 5))

    events = engine.process(buy(101, 8, validity=Validity.IOC))

    assert events == [
        OrderAccepted(order_id=2, side=Side.BUY, price=101, quantity=8),
        Trade(
            trade_id=1,
            price=100,
            quantity=5,
            maker_order_id=1,
            taker_order_id=2,
            aggressor=Side.BUY,
        ),
        OrderCancelled(order_id=2, quantity=3, reason=CancelReason.UNFILLED_IOC),
    ]
    assert resting(engine, Side.BUY) == []


def test_ioc_order_that_cannot_trade_is_cancelled_in_full() -> None:
    engine = new_engine()
    engine.process(sell(101, 5))

    events = engine.process(buy(100, 5, validity=Validity.IOC))

    assert events == [
        OrderAccepted(order_id=2, side=Side.BUY, price=100, quantity=5),
        OrderCancelled(order_id=2, quantity=5, reason=CancelReason.UNFILLED_IOC),
    ]
    assert resting(engine, Side.SELL) == [(101, 1, 5)]


def test_ioc_order_that_fills_completely_leaves_nothing_to_cancel() -> None:
    engine = new_engine()
    engine.process(buy(100, 10))

    events = engine.process(sell(100, 4, validity=Validity.IOC))

    assert not any(isinstance(e, OrderCancelled) for e in events)
    assert resting(engine, Side.BUY) == [(100, 1, 6)]


def test_day_order_rests_whatever_it_cannot_fill() -> None:
    engine = new_engine()
    engine.process(sell(100, 5))

    events = engine.process(buy(101, 8, validity=Validity.DAY))

    assert not any(isinstance(e, OrderCancelled) for e in events)
    assert resting(engine, Side.BUY) == [(101, 2, 3)]
