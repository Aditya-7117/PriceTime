"""Self-trade prevention, as NSE's STPC works: each order chooses what is cancelled."""

from pricetime.commands import ModifyOrder
from pricetime.events import (
    CancelReason,
    OrderAccepted,
    OrderCancelled,
    OrderModified,
    Trade,
)
from pricetime.orders import SelfTradeAction, Side
from tests.support import buy, market_buy, new_engine, resting, sell

SAME_CLIENT = 7
PASSIVE = SelfTradeAction.CANCEL_PASSIVE


def test_cancel_active_cancels_the_incoming_order_and_leaves_the_resting_one() -> None:
    engine = new_engine()
    engine.process(sell(100, 5, client=SAME_CLIENT))

    events = engine.process(buy(100, 5, client=SAME_CLIENT))

    assert events == [
        OrderAccepted(order_id=2, side=Side.BUY, price=100, quantity=5),
        OrderCancelled(order_id=2, quantity=5, reason=CancelReason.SELF_TRADE),
    ]
    assert resting(engine, Side.SELL) == [(100, 1, 5)]
    assert resting(engine, Side.BUY) == []


def test_cancel_active_keeps_the_trades_made_before_it_met_its_own_order() -> None:
    engine = new_engine()
    engine.process(sell(100, 5))
    engine.process(sell(101, 5, client=SAME_CLIENT))

    events = engine.process(buy(101, 10, client=SAME_CLIENT))

    assert events[1:] == [
        Trade(
            trade_id=1,
            price=100,
            quantity=5,
            maker_order_id=1,
            taker_order_id=3,
            aggressor=Side.BUY,
        ),
        OrderCancelled(order_id=3, quantity=5, reason=CancelReason.SELF_TRADE),
    ]
    assert resting(engine, Side.SELL) == [(101, 2, 5)]


def test_cancel_passive_cancels_the_resting_order_and_keeps_matching() -> None:
    engine = new_engine()
    engine.process(sell(100, 5, client=SAME_CLIENT))
    engine.process(sell(100, 5))

    events = engine.process(buy(100, 5, client=SAME_CLIENT, self_trade=PASSIVE))

    assert events[1:] == [
        OrderCancelled(order_id=1, quantity=5, reason=CancelReason.SELF_TRADE),
        Trade(
            trade_id=1,
            price=100,
            quantity=5,
            maker_order_id=2,
            taker_order_id=3,
            aggressor=Side.BUY,
        ),
    ]
    assert len(engine.book) == 0


def test_cancel_passive_clears_every_one_of_its_own_orders_in_the_way() -> None:
    engine = new_engine()
    engine.process(sell(100, 3, client=SAME_CLIENT))
    engine.process(sell(100, 4, client=SAME_CLIENT))
    engine.process(sell(101, 5))

    events = engine.process(buy(101, 5, client=SAME_CLIENT, self_trade=PASSIVE))

    cancelled = [e.order_id for e in events if isinstance(e, OrderCancelled)]
    traded = [(e.maker_order_id, e.quantity) for e in events if isinstance(e, Trade)]
    assert cancelled == [1, 2]
    assert traded == [(3, 5)]


def test_an_order_rests_if_cancel_passive_clears_the_other_side() -> None:
    engine = new_engine()
    engine.process(sell(100, 5, client=SAME_CLIENT))

    engine.process(buy(100, 5, client=SAME_CLIENT, self_trade=PASSIVE))

    assert resting(engine, Side.BUY) == [(100, 2, 5)]
    assert resting(engine, Side.SELL) == []


def test_a_market_order_that_meets_its_own_client_is_cancelled_by_cancel_active() -> None:
    engine = new_engine()
    engine.process(sell(101, 5, client=SAME_CLIENT))

    events = engine.process(market_buy(5, client=SAME_CLIENT))

    assert events[1:] == [OrderCancelled(order_id=2, quantity=5, reason=CancelReason.SELF_TRADE)]
    assert resting(engine, Side.SELL) == [(101, 1, 5)]


def test_a_repriced_order_that_meets_its_own_client_is_cancelled_by_its_own_choice() -> None:
    engine = new_engine()
    engine.process(buy(99, 5, client=SAME_CLIENT))
    engine.process(sell(101, 5, client=SAME_CLIENT))

    events = engine.process(ModifyOrder(order_id=1, price=101, quantity=5))

    assert events == [
        OrderModified(order_id=1, price=101, quantity=5, remaining=5, kept_priority=False),
        OrderCancelled(order_id=1, quantity=5, reason=CancelReason.SELF_TRADE),
    ]
    assert resting(engine, Side.BUY) == []
    assert resting(engine, Side.SELL) == [(101, 2, 5)]


def test_a_repriced_cancel_passive_order_clears_its_own_order_and_rests() -> None:
    engine = new_engine()
    engine.process(buy(99, 5, client=SAME_CLIENT, self_trade=PASSIVE))
    engine.process(sell(101, 5, client=SAME_CLIENT))

    events = engine.process(ModifyOrder(order_id=1, price=101, quantity=5))

    assert events[1:] == [OrderCancelled(order_id=2, quantity=5, reason=CancelReason.SELF_TRADE)]
    assert resting(engine, Side.BUY) == [(101, 1, 5)]
    assert resting(engine, Side.SELL) == []
