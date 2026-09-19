"""Checks that must hold after every command, whatever the command sequence."""

from dataclasses import dataclass
from itertools import pairwise

from pricetime.book import BookSide
from pricetime.commands import Command, ModifyOrder, NewLimitOrder, NewMarketOrder, Validity
from pricetime.engine import MatchingEngine
from pricetime.events import (
    CancelReason,
    CancelRejected,
    Event,
    MarketOrderConverted,
    ModifyRejected,
    OrderAccepted,
    OrderCancelled,
    OrderModified,
    OrderRejected,
    Trade,
)
from pricetime.orders import SelfTradeAction, Side
from pricetime.rules import MarketRules
from pricetime.snapshot import EngineSnapshot, RestingOrder


def resting_orders(snapshot: EngineSnapshot) -> tuple[RestingOrder, ...]:
    return snapshot.bids + snapshot.asks


def check_not_crossed(engine: MatchingEngine) -> None:
    """No resting buy is priced at or above a resting sell."""
    best_bid, best_ask = engine.book.best_bid(), engine.book.best_ask()
    if best_bid is not None and best_ask is not None:
        assert best_bid < best_ask, f"crossed book: bid {best_bid} >= ask {best_ask}"


def check_within_band(engine: MatchingEngine, rules: MarketRules) -> None:
    """Every resting order is priced inside the day's price band."""
    if rules.price_band is None:
        return
    for order in resting_orders(engine.snapshot()):
        assert rules.price_band.contains(order.price), f"order {order.order_id} outside band"


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
                case MarketOrderConverted():
                    assert event.remaining == self.open[event.order_id] > 0
                case OrderRejected() | CancelRejected() | ModifyRejected():
                    pass

    def check(self, engine: MatchingEngine) -> None:
        open_orders = {order_id: qty for order_id, qty in self.open.items() if qty > 0}
        on_book = {o.order_id: o for o in resting_orders(engine.snapshot())}
        assert {order_id: o.remaining for order_id, o in on_book.items()} == open_orders
        for order_id, order in on_book.items():
            assert order.filled == self.filled[order_id]


def check_price_time_priority(
    before: EngineSnapshot, command: Command, events: list[Event], rules: MarketRules
) -> None:
    """The incoming order worked through the opposite side strictly in priority order.

    The snapshot before the command lists the opposite side in priority order,
    so an incoming order must touch a prefix of that list, all within its limit.
    Each resting order it touches is either traded with, at the resting order's
    own price, or, if it belongs to the incoming order's own client under
    cancel passive, cancelled. Every order it trades with but the last fills
    completely. If the incoming order is left with open quantity, the next
    resting order must be beyond its limit, or be its own client's order under
    cancel active, or there must be none.
    """
    incoming = _incoming_order(before, command, events, rules)
    touched = [
        e
        for e in events
        if isinstance(e, Trade)
        or (
            isinstance(e, OrderCancelled)
            and e.reason is CancelReason.SELF_TRADE
            and (incoming is None or e.order_id != incoming.order_id)
        )
    ]
    if incoming is None:
        assert not touched, "a command that does not take liquidity touched the book"
        return
    queue = before.asks if incoming.side is Side.BUY else before.bids

    assert len(touched) <= len(queue)
    for maker, event in zip(queue, touched, strict=False):
        assert _within_limit(incoming.side, incoming.limit, maker.price)
        if isinstance(event, Trade):
            assert event.maker_order_id == maker.order_id, "traded out of priority order"
            assert event.price == maker.price
            assert event.aggressor is incoming.side
            assert maker.client_id != incoming.client_id, "traded with its own client"
        else:
            assert event.order_id == maker.order_id, "cancelled out of priority order"
            assert maker.client_id == incoming.client_id
            assert incoming.self_trade is SelfTradeAction.CANCEL_PASSIVE
            assert event.quantity == maker.remaining
    for maker, event in zip(queue, touched[:-1], strict=False):
        if isinstance(event, Trade):
            assert event.quantity == maker.remaining, "moved on before the maker filled"

    filled = sum(e.quantity for e in touched if isinstance(e, Trade))
    assert filled <= incoming.quantity
    if filled == incoming.quantity:
        return
    last = touched[-1] if touched else None
    if isinstance(last, Trade):
        assert last.quantity == queue[len(touched) - 1].remaining
    stopped_by_self_trade = any(
        isinstance(e, OrderCancelled)
        and e.order_id == incoming.order_id
        and e.reason is CancelReason.SELF_TRADE
        for e in events
    )
    if len(touched) < len(queue):
        following = queue[len(touched)]
        crossing = _within_limit(incoming.side, incoming.limit, following.price)
        own = (
            following.client_id == incoming.client_id
            and incoming.self_trade is SelfTradeAction.CANCEL_ACTIVE
        )
        assert stopped_by_self_trade == (crossing and own)
        assert not crossing or own, "stopped while still crossing"
    else:
        assert not stopped_by_self_trade


@dataclass(frozen=True, slots=True)
class _Incoming:
    order_id: int
    side: Side
    limit: int
    quantity: int
    client_id: int
    self_trade: SelfTradeAction


def _incoming_order(
    before: EngineSnapshot, command: Command, events: list[Event], rules: MarketRules
) -> _Incoming | None:
    """The order this command sent into the book to trade, if it sent one."""
    if not events:
        return None
    first = events[0]
    match command:
        case NewLimitOrder() if isinstance(first, OrderAccepted):
            return _Incoming(
                first.order_id,
                command.side,
                command.price,
                command.quantity,
                command.client_id,
                command.self_trade,
            )
        case NewMarketOrder() if isinstance(first, OrderAccepted):
            assert before.last_trade_price is not None, "market order accepted with no band"
            limit = protection_limit(command.side, before.last_trade_price, rules)
            return _Incoming(
                first.order_id,
                command.side,
                limit,
                command.quantity,
                command.client_id,
                command.self_trade,
            )
        case ModifyOrder() if (
            isinstance(first, OrderModified) and not first.kept_priority and first.remaining
        ):
            (order,) = (o for o in resting_orders(before) if o.order_id == command.order_id)
            return _Incoming(
                order.order_id,
                order.side,
                command.price,
                first.remaining,
                order.client_id,
                order.self_trade,
            )
        case _:
            return None


def _within_limit(side: Side, limit: int, price: int) -> bool:
    return price <= limit if side is Side.BUY else price >= limit


def protection_limit(side: Side, last_trade_price: int, rules: MarketRules) -> int:
    """NSE's band, from its circular: X% of the last trade, at least the minimum width."""
    width = max(last_trade_price * rules.protection_bps // 10_000, rules.protection_min_ticks)
    return last_trade_price + width if side is Side.BUY else last_trade_price - width


def check_market_order_outcome(
    before: EngineSnapshot, after: EngineSnapshot, command: NewMarketOrder, events: list[Event]
) -> None:
    """What happens to a market order's remainder depends only on the book it leaves behind.

    Orders still beyond the band on the other side: cancelled for price
    protection. Otherwise the other side is empty, and an IOC order is cancelled
    while a DAY order rests at the best price on its own side, or at the last
    traded price if its own side is empty too.
    """
    if not events or not isinstance(events[0], OrderAccepted):
        return
    order_id = events[0].order_id
    outcome = events[-1]
    opposite = after.asks if command.side is Side.BUY else after.bids
    own_before = before.bids if command.side is Side.BUY else before.asks
    match outcome:
        case OrderCancelled(order_id=cancelled, reason=CancelReason.PRICE_PROTECTION):
            assert cancelled == order_id
            assert opposite, "cancelled for protection with nothing beyond the band"
        case OrderCancelled(order_id=cancelled, reason=CancelReason.SELF_TRADE):
            assert cancelled == order_id
            assert command.self_trade is SelfTradeAction.CANCEL_ACTIVE
            assert opposite, "stopped for a self-trade with nothing left to meet"
            assert opposite[0].client_id == command.client_id
        case OrderCancelled(order_id=cancelled, reason=CancelReason.UNFILLED_IOC):
            assert cancelled == order_id
            assert command.validity is Validity.IOC
            assert not opposite
        case MarketOrderConverted(order_id=converted, price=price):
            assert converted == order_id
            assert command.validity is Validity.DAY
            assert not opposite
            expected = own_before[0].price if own_before else after.last_trade_price
            assert price == expected
            own_after = after.bids if command.side is Side.BUY else after.asks
            level = [o for o in own_after if o.price == price]
            assert level[-1].order_id == order_id, "converted order is not last in its queue"
        case _:
            assert isinstance(outcome, OrderAccepted | Trade)
            assert sum(e.quantity for e in events if isinstance(e, Trade)) == command.quantity


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
