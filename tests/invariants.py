"""Checks that must hold after every command, whatever the command sequence."""

from itertools import pairwise

from pricetime.book import BookSide
from pricetime.commands import Command, ModifyOrder, NewLimitOrder, NewMarketOrder
from pricetime.engine import MatchingEngine
from pricetime.events import (
    CancelRejected,
    Event,
    ModifyRejected,
    OrderAccepted,
    OrderCancelled,
    OrderModified,
    OrderRejected,
    Trade,
)
from pricetime.orders import Side
from pricetime.snapshot import EngineSnapshot, RestingOrder


def resting_orders(snapshot: EngineSnapshot) -> tuple[RestingOrder, ...]:
    return snapshot.bids + snapshot.asks


def check_not_crossed(engine: MatchingEngine) -> None:
    """No resting buy is priced at or above a resting sell."""
    best_bid, best_ask = engine.book.best_bid(), engine.book.best_ask()
    if best_bid is not None and best_ask is not None:
        assert best_bid < best_ask, f"crossed book: bid {best_bid} >= ask {best_ask}"


def check_structure(engine: MatchingEngine) -> None:
    """Levels, queue links, level totals and the ID index all agree with each other."""
    counted = 0
    for side in (engine.book.bids, engine.book.asks):
        counted += _check_side(engine, side)
    assert counted == len(engine.book), "ID index holds orders that are not on any level"


def _check_side(engine: MatchingEngine, side: BookSide) -> int:
    level_prices = [level.price for level in side]
    ordered = sorted(level_prices, reverse=side.side is Side.BUY)
    assert level_prices == ordered, f"{side.side} levels out of priority order"
    assert len(set(level_prices)) == len(level_prices), "two levels share a price"

    counted = 0
    for level in side:
        orders = list(level)
        assert orders, f"empty level left at {level.price}"
        assert len(level) == len(orders)
        assert level.total_quantity == sum(order.remaining for order in orders)
        assert level.head is orders[0]
        assert level.tail is orders[-1]
        for ahead, behind in pairwise(orders):
            assert ahead.next is behind
            assert behind.prev is ahead
            assert ahead.priority < behind.priority, "queue is not in arrival order"
        for order in orders:
            assert order.side is side.side
            assert order.price == level.price
            assert order.remaining > 0
            assert engine.book.get(order.order_id) is order
        counted += len(orders)
    return counted


class Ledger:
    """Each order's open and filled quantity, rebuilt from commands and events alone.

    The ledger never looks inside the engine. After every command the book must
    hold exactly the orders the ledger says are open, with exactly those
    quantities: nothing appears without an event, and nothing vanishes without
    being filled or cancelled.
    """

    def __init__(self) -> None:
        self.open: dict[int, int] = {}
        self.filled: dict[int, int] = {}

    def record(self, command: Command, events: list[Event]) -> None:
        for event in events:
            match event:
                case OrderAccepted():
                    assert isinstance(command, NewLimitOrder | NewMarketOrder)
                    assert event.quantity == command.quantity
                    self.open[event.order_id] = event.quantity
                    self.filled[event.order_id] = 0
                case Trade():
                    assert event.quantity > 0
                    for order_id in (event.maker_order_id, event.taker_order_id):
                        self.open[order_id] -= event.quantity
                        self.filled[order_id] += event.quantity
                        assert self.open[order_id] >= 0, f"order {order_id} overfilled"
                case OrderCancelled():
                    assert event.quantity > 0
                    assert event.quantity == self.open[event.order_id]
                    self.open[event.order_id] = 0
                case OrderModified():
                    assert isinstance(command, ModifyOrder)
                    assert event.quantity == command.quantity
                    expected = max(0, command.quantity - self.filled[event.order_id])
                    assert event.remaining == expected
                    self.open[event.order_id] = event.remaining
                case OrderRejected() | CancelRejected() | ModifyRejected():
                    pass

    def check(self, engine: MatchingEngine) -> None:
        open_orders = {order_id: qty for order_id, qty in self.open.items() if qty > 0}
        on_book = {o.order_id: o for o in resting_orders(engine.snapshot())}
        assert {order_id: o.remaining for order_id, o in on_book.items()} == open_orders
        for order_id, order in on_book.items():
            assert order.filled == self.filled[order_id]


def check_price_time_priority(
    before: EngineSnapshot, command: Command, events: list[Event]
) -> None:
    """Trades took the opposite side strictly in priority order, and stopped only when they had to.

    The snapshot before the command lists the opposite side in priority order,
    so an incoming order must trade with a prefix of that list: every maker but
    the last filled completely, at the maker's own price, all within the
    incoming order's limit. If the incoming order is left with open quantity,
    the next maker in line must be beyond its limit, or there must be none.
    """
    trades = [event for event in events if isinstance(event, Trade)]
    incoming = _incoming_order(before, command, events)
    if incoming is None:
        assert not trades, "trades from a command that does not take liquidity"
        return
    side, limit, quantity = incoming
    queue = before.asks if side is Side.BUY else before.bids

    assert len(trades) <= len(queue)
    for maker, trade in zip(queue, trades, strict=False):
        assert trade.maker_order_id == maker.order_id, "traded out of priority order"
        assert trade.price == maker.price
        assert trade.aggressor is side
        assert _within_limit(side, limit, trade.price)
    for maker, trade in zip(queue, trades[:-1], strict=False):
        assert trade.quantity == maker.remaining, "moved on before the maker filled"

    filled = sum(trade.quantity for trade in trades)
    assert filled <= quantity
    if filled < quantity:
        if trades:
            assert trades[-1].quantity == queue[len(trades) - 1].remaining
        if len(trades) < len(queue):
            next_price = queue[len(trades)].price
            assert not _within_limit(side, limit, next_price), "stopped while still crossing"


def _incoming_order(
    before: EngineSnapshot, command: Command, events: list[Event]
) -> tuple[Side, int | None, int] | None:
    """The side, limit and quantity of an order this command sent into the book to trade."""
    if not events:
        return None
    first = events[0]
    match command:
        case NewLimitOrder() if isinstance(first, OrderAccepted):
            return command.side, command.price, command.quantity
        case NewMarketOrder() if isinstance(first, OrderAccepted):
            return command.side, None, command.quantity
        case ModifyOrder() if (
            isinstance(first, OrderModified) and not first.kept_priority and first.remaining
        ):
            (order,) = (o for o in resting_orders(before) if o.order_id == command.order_id)
            return order.side, command.price, first.remaining
        case _:
            return None


def _within_limit(side: Side, limit: int | None, price: int) -> bool:
    if limit is None:
        return True
    return price <= limit if side is Side.BUY else price >= limit


def check_modify_priority(
    before: EngineSnapshot, after: EngineSnapshot, command: ModifyOrder, events: list[Event]
) -> None:
    """A modify keeps its queue place only if the price holds and open quantity does not grow.

    Otherwise the order either left the book, or it is now the last order at its
    price, exactly where a brand new order would be.
    """
    if not events or not isinstance(events[0], OrderModified):
        return
    modified = events[0]
    (old,) = (o for o in resting_orders(before) if o.order_id == command.order_id)
    now = next((o for o in resting_orders(after) if o.order_id == command.order_id), None)

    shrinks_in_place = command.price == old.price and 0 < modified.remaining <= old.remaining
    assert modified.kept_priority == shrinks_in_place
    if modified.remaining == 0:
        assert now is None
    elif modified.kept_priority:
        assert now is not None
        assert now.priority == old.priority
        assert now.remaining == modified.remaining
    elif now is not None:
        assert now.priority == after.sequence
        level = [o for o in resting_orders(after) if o.side is now.side and o.price == now.price]
        assert level[-1] is now, "re-entered order is not at the back of its level"
