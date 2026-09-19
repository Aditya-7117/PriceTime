import pytest

from pricetime.commands import ModifyOrder
from pricetime.engine import MatchingEngine
from pricetime.events import ModifyRejected, OrderModified, RejectReason, Trade
from pricetime.orders import Side
from pricetime.rules import MarketRules, PriceBand
from tests.support import buy, new_engine, resting, sell


def priorities(engine: MatchingEngine) -> dict[int, int]:
    snapshot = engine.snapshot()
    return {order.order_id: order.priority for order in snapshot.bids + snapshot.asks}


def test_reducing_quantity_at_the_same_price_keeps_queue_position() -> None:
    engine = new_engine()
    engine.process(buy(100, 10))
    engine.process(buy(100, 10))

    events = engine.process(ModifyOrder(order_id=1, price=100, quantity=4))

    assert events == [
        OrderModified(order_id=1, price=100, quantity=4, remaining=4, kept_priority=True)
    ]
    assert resting(engine, Side.BUY) == [(100, 1, 4), (100, 2, 10)]
    assert priorities(engine) == {1: 1, 2: 2}


def test_reduced_order_still_fills_first() -> None:
    engine = new_engine()
    engine.process(buy(100, 10))
    engine.process(buy(100, 10))
    engine.process(ModifyOrder(order_id=1, price=100, quantity=4))

    events = engine.process(sell(100, 5))

    trades = [(e.maker_order_id, e.quantity) for e in events if isinstance(e, Trade)]
    assert trades == [(1, 4), (2, 1)]


def test_increasing_quantity_loses_queue_position() -> None:
    engine = new_engine()
    engine.process(buy(100, 10))
    engine.process(buy(100, 10))

    events = engine.process(ModifyOrder(order_id=1, price=100, quantity=15))

    assert events == [
        OrderModified(order_id=1, price=100, quantity=15, remaining=15, kept_priority=False)
    ]
    assert resting(engine, Side.BUY) == [(100, 2, 10), (100, 1, 15)]
    assert priorities(engine) == {2: 2, 1: 3}


def test_changing_price_loses_queue_position_and_joins_the_back_of_the_new_level() -> None:
    engine = new_engine()
    engine.process(buy(100, 10))
    engine.process(buy(101, 10))

    events = engine.process(ModifyOrder(order_id=1, price=101, quantity=10))

    assert events == [
        OrderModified(order_id=1, price=101, quantity=10, remaining=10, kept_priority=False)
    ]
    assert resting(engine, Side.BUY) == [(101, 2, 10), (101, 1, 10)]


def test_modify_with_the_same_price_and_quantity_keeps_position() -> None:
    engine = new_engine()
    engine.process(sell(100, 10))
    engine.process(sell(100, 10))

    events = engine.process(ModifyOrder(order_id=1, price=100, quantity=10))

    assert events == [
        OrderModified(order_id=1, price=100, quantity=10, remaining=10, kept_priority=True)
    ]
    assert resting(engine, Side.SELL) == [(100, 1, 10), (100, 2, 10)]


def test_price_change_that_crosses_trades_immediately_as_the_taker() -> None:
    engine = new_engine()
    engine.process(buy(99, 10))
    engine.process(sell(101, 5))

    events = engine.process(ModifyOrder(order_id=1, price=101, quantity=10))

    assert events == [
        OrderModified(order_id=1, price=101, quantity=10, remaining=10, kept_priority=False),
        Trade(
            trade_id=1,
            price=101,
            quantity=5,
            maker_order_id=2,
            taker_order_id=1,
            aggressor=Side.BUY,
        ),
    ]
    assert resting(engine, Side.BUY) == [(101, 1, 5)]
    assert resting(engine, Side.SELL) == []
    (order,) = engine.snapshot().bids
    assert order.filled == 5


def test_crossing_modify_that_fills_completely_leaves_the_book() -> None:
    engine = new_engine()
    engine.process(sell(101, 10))
    engine.process(buy(100, 10))

    engine.process(ModifyOrder(order_id=2, price=101, quantity=10))

    assert len(engine.book) == 0


def test_quantity_is_the_new_total_including_what_has_already_filled() -> None:
    # The owner asks for 8 in total. 4 have filled, so 4 stay open. Reading the
    # 8 as open quantity would have let a replace racing a fill buy 12.
    engine = new_engine()
    engine.process(sell(100, 10))
    engine.process(buy(100, 4))

    events = engine.process(ModifyOrder(order_id=1, price=100, quantity=8))

    assert events == [
        OrderModified(order_id=1, price=100, quantity=8, remaining=4, kept_priority=True)
    ]
    assert resting(engine, Side.SELL) == [(100, 1, 4)]


@pytest.mark.parametrize("quantity", [4, 3])
def test_modifying_to_at_or_below_the_filled_quantity_finishes_the_order(quantity: int) -> None:
    engine = new_engine()
    engine.process(sell(100, 10))
    engine.process(buy(100, 4))

    events = engine.process(ModifyOrder(order_id=1, price=100, quantity=quantity))

    assert events == [
        OrderModified(order_id=1, price=100, quantity=quantity, remaining=0, kept_priority=False)
    ]
    assert len(engine.book) == 0


def test_modify_for_an_id_never_issued_is_rejected_as_unknown() -> None:
    engine = new_engine()

    events = engine.process(ModifyOrder(order_id=5, price=100, quantity=10))

    assert events == [ModifyRejected(order_id=5, reason=RejectReason.UNKNOWN_ORDER)]


def test_modify_after_the_order_filled_is_too_late() -> None:
    engine = new_engine()
    engine.process(sell(100, 10))
    engine.process(buy(100, 10))

    events = engine.process(ModifyOrder(order_id=1, price=100, quantity=20))

    assert events == [ModifyRejected(order_id=1, reason=RejectReason.TOO_LATE)]
    assert len(engine.book) == 0


@pytest.mark.parametrize(
    ("price", "quantity", "reason"),
    [
        (100, 0, RejectReason.INVALID_QUANTITY),
        (100, -3, RejectReason.INVALID_QUANTITY),
        (0, 10, RejectReason.INVALID_PRICE),
        (-5, 10, RejectReason.INVALID_PRICE),
    ],
)
def test_invalid_modify_is_rejected_and_leaves_the_order_unchanged(
    price: int, quantity: int, reason: RejectReason
) -> None:
    engine = new_engine()
    engine.process(buy(100, 10))

    events = engine.process(ModifyOrder(order_id=1, price=price, quantity=quantity))

    assert events == [ModifyRejected(order_id=1, reason=reason)]
    assert resting(engine, Side.BUY) == [(100, 1, 10)]
    assert priorities(engine) == {1: 1}


def test_modify_to_a_price_outside_the_band_is_rejected_and_changes_nothing() -> None:
    engine = new_engine(MarketRules(price_band=PriceBand(lower=90, upper=110)))
    engine.process(buy(100, 10))

    events = engine.process(ModifyOrder(order_id=1, price=111, quantity=10))

    assert events == [ModifyRejected(order_id=1, reason=RejectReason.PRICE_OUT_OF_BAND)]
    assert resting(engine, Side.BUY) == [(100, 1, 10)]
