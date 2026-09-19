"""Market orders under NSE's market price protection (circular 155/2022)."""

import pytest

from pricetime.commands import Validity
from pricetime.events import (
    CancelReason,
    MarketOrderConverted,
    OrderAccepted,
    OrderCancelled,
    OrderRejected,
    RejectReason,
    Trade,
)
from pricetime.orders import Side
from pricetime.rules import MarketRules
from tests.support import buy, market_buy, market_sell, new_engine, resting, sell

# Last traded price 100 and a 5% band, so market buys may trade up to 105 and sells down to 95.
RULES = MarketRules(protection_bps=500, protection_min_ticks=2, opening_price=100)


def bought(trade_id: int, price: int, quantity: int, maker: int, taker: int) -> Trade:
    return Trade(
        trade_id=trade_id,
        price=price,
        quantity=quantity,
        maker_order_id=maker,
        taker_order_id=taker,
        aggressor=Side.BUY,
    )


def sold(trade_id: int, price: int, quantity: int, maker: int, taker: int) -> Trade:
    return Trade(
        trade_id=trade_id,
        price=price,
        quantity=quantity,
        maker_order_id=maker,
        taker_order_id=taker,
        aggressor=Side.SELL,
    )


def test_market_order_is_rejected_before_anything_has_traded() -> None:
    engine = new_engine(MarketRules(protection_bps=500, protection_min_ticks=2))
    engine.process(sell(100, 5))

    events = engine.process(market_buy(5))

    assert events == [OrderRejected(order_id=2, reason=RejectReason.NO_LAST_TRADE_PRICE)]
    assert resting(engine, Side.SELL) == [(100, 1, 5)]


def test_the_first_trade_opens_the_market_to_market_orders() -> None:
    engine = new_engine(MarketRules(protection_bps=500, protection_min_ticks=2))
    engine.process(sell(100, 5))
    engine.process(buy(100, 2))

    events = engine.process(market_buy(3))

    assert events[1:] == [bought(2, 100, 3, maker=1, taker=3)]


def test_market_order_sweeps_up_to_the_band_and_cancels_the_rest_if_orders_lie_beyond() -> None:
    engine = new_engine(RULES)
    engine.process(sell(101, 5))
    engine.process(sell(104, 5))
    engine.process(sell(106, 5))

    events = engine.process(market_buy(12))

    assert events == [
        OrderAccepted(order_id=4, side=Side.BUY, price=None, quantity=12),
        bought(1, 101, 5, maker=1, taker=4),
        bought(2, 104, 5, maker=2, taker=4),
        OrderCancelled(order_id=4, quantity=2, reason=CancelReason.PRICE_PROTECTION),
    ]
    assert resting(engine, Side.SELL) == [(106, 3, 5)]
    assert resting(engine, Side.BUY) == []


def test_sell_market_order_is_protected_below_the_last_traded_price() -> None:
    engine = new_engine(RULES)
    engine.process(buy(96, 5))
    engine.process(buy(94, 5))

    events = engine.process(market_sell(8))

    assert events[1:] == [
        sold(1, 96, 5, maker=1, taker=3),
        OrderCancelled(order_id=3, quantity=3, reason=CancelReason.PRICE_PROTECTION),
    ]
    assert resting(engine, Side.BUY) == [(94, 2, 5)]


def test_day_market_order_rests_at_its_own_sides_best_price_when_the_other_side_empties() -> None:
    engine = new_engine(RULES)
    engine.process(buy(99, 5))
    engine.process(sell(101, 5))

    events = engine.process(market_buy(8))

    assert events == [
        OrderAccepted(order_id=3, side=Side.BUY, price=None, quantity=8),
        bought(1, 101, 5, maker=2, taker=3),
        MarketOrderConverted(order_id=3, price=99, remaining=3),
    ]
    assert resting(engine, Side.BUY) == [(99, 1, 5), (99, 3, 3)]


def test_day_market_order_rests_at_the_last_traded_price_when_the_book_empties() -> None:
    engine = new_engine(RULES)
    engine.process(sell(101, 5))

    events = engine.process(market_buy(8))

    assert events[-1] == MarketOrderConverted(order_id=2, price=101, remaining=3)
    assert resting(engine, Side.BUY) == [(101, 2, 3)]


def test_day_market_order_into_an_empty_book_rests_at_the_last_traded_price() -> None:
    engine = new_engine(RULES)

    events = engine.process(market_sell(4))

    assert events == [
        OrderAccepted(order_id=1, side=Side.SELL, price=None, quantity=4),
        MarketOrderConverted(order_id=1, price=100, remaining=4),
    ]
    assert resting(engine, Side.SELL) == [(100, 1, 4)]


def test_ioc_market_order_cancels_its_remainder_when_the_other_side_runs_out() -> None:
    engine = new_engine(RULES)
    engine.process(sell(101, 5))

    events = engine.process(market_buy(8, validity=Validity.IOC))

    assert events[-1] == OrderCancelled(order_id=2, quantity=3, reason=CancelReason.UNFILLED_IOC)
    assert len(engine.book) == 0


def test_ioc_market_order_facing_orders_beyond_the_band_is_cancelled_for_protection() -> None:
    engine = new_engine(RULES)
    engine.process(sell(106, 5))

    events = engine.process(market_buy(5, validity=Validity.IOC))

    assert events[-1] == OrderCancelled(
        order_id=2, quantity=5, reason=CancelReason.PRICE_PROTECTION
    )


def test_the_band_never_narrows_below_its_minimum_width() -> None:
    engine = new_engine(MarketRules(protection_bps=500, protection_min_ticks=2, opening_price=10))
    engine.process(sell(12, 5))

    events = engine.process(market_buy(5))

    assert events[1:] == [bought(1, 12, 5, maker=1, taker=2)]


def test_the_band_is_set_from_the_last_trade_before_the_order_arrives() -> None:
    engine = new_engine(RULES)
    engine.process(sell(104, 5))
    engine.process(sell(107, 5))

    # The sweep moves the last traded price to 104, but the band stays at 105.
    events = engine.process(market_buy(10))

    trades = [(e.price, e.quantity) for e in events if isinstance(e, Trade)]
    assert trades == [(104, 5)]
    assert events[-1] == OrderCancelled(
        order_id=3, quantity=5, reason=CancelReason.PRICE_PROTECTION
    )


def test_every_trade_moves_the_last_traded_price() -> None:
    engine = new_engine(RULES)
    engine.process(sell(101, 5))
    engine.process(sell(102, 5))
    assert engine.snapshot().last_trade_price == 100

    engine.process(buy(102, 8))

    assert engine.snapshot().last_trade_price == 102


def test_filled_market_order_leaves_nothing_to_cancel_or_rest() -> None:
    engine = new_engine(RULES)
    engine.process(buy(100, 10))

    events = engine.process(market_sell(4))

    assert events[1:] == [sold(1, 100, 4, maker=1, taker=2)]
    assert resting(engine, Side.BUY) == [(100, 1, 6)]
    assert resting(engine, Side.SELL) == []


@pytest.mark.parametrize("quantity", [0, -1])
def test_market_order_with_non_positive_quantity_is_rejected(quantity: int) -> None:
    engine = new_engine(RULES)
    engine.process(sell(100, 5))

    events = engine.process(market_buy(quantity))

    assert events == [OrderRejected(order_id=2, reason=RejectReason.INVALID_QUANTITY)]
    assert resting(engine, Side.SELL) == [(100, 1, 5)]
