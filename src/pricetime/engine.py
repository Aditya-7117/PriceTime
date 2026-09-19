"""The matching engine: applies commands to the book under price-time priority."""

from typing import assert_never

from pricetime.book import BookSide, OrderBook
from pricetime.commands import (
    CancelOrder,
    Command,
    ModifyOrder,
    NewLimitOrder,
    NewMarketOrder,
    Validity,
)
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
    RejectReason,
    Trade,
)
from pricetime.orders import Order, Side
from pricetime.rules import MarketRules
from pricetime.snapshot import EngineSnapshot, RestingOrder


class MatchingEngine:
    """A single-instrument matching engine for one trading session.

    Commands are processed one at a time, to completion, in the order they are
    given. Each call to `process` returns the events that command caused and
    calls nothing else, so no outside code can run, or re-enter the engine, in
    the middle of a match. The engine reads no clock and no randomness: time
    priority comes from the order of commands, so the same rules and the same
    commands always produce the same events and the same book.
    """

    def __init__(self, rules: MarketRules) -> None:
        self._rules = rules
        self._book = OrderBook()
        self._next_order_id = 1
        self._next_trade_id = 1
        self._sequence = 0
        self._last_trade_price = rules.opening_price

    @property
    def book(self) -> OrderBook:
        """The live book, for inspection. The engine owns it; callers must not mutate it."""
        return self._book

    def process(self, command: Command) -> list[Event]:
        """Apply one command and return the events it caused, in order."""
        self._sequence += 1
        match command:
            case NewLimitOrder():
                return self._new_limit(command)
            case NewMarketOrder():
                return self._new_market(command)
            case CancelOrder():
                return self._cancel(command)
            case ModifyOrder():
                return self._modify(command)
            case _:
                assert_never(command)

    def snapshot(self) -> EngineSnapshot:
        """Copy the engine's full state into an immutable, comparable value."""
        return EngineSnapshot(
            bids=_resting_orders(self._book.bids),
            asks=_resting_orders(self._book.asks),
            next_order_id=self._next_order_id,
            next_trade_id=self._next_trade_id,
            sequence=self._sequence,
            last_trade_price=self._last_trade_price,
        )

    def _new_limit(self, command: NewLimitOrder) -> list[Event]:
        order_id = self._issue_order_id()
        if command.quantity <= 0:
            return [OrderRejected(order_id=order_id, reason=RejectReason.INVALID_QUANTITY)]
        if price_problem := self._price_problem(command.price):
            return [OrderRejected(order_id=order_id, reason=price_problem)]
        events: list[Event] = [
            OrderAccepted(
                order_id=order_id,
                side=command.side,
                price=command.price,
                quantity=command.quantity,
            )
        ]
        order = self._incoming(order_id, command.side, command.price, command.quantity)
        self._sweep(events, order)
        if not order.remaining:
            return events
        if command.validity is Validity.IOC:
            events.append(_cancelled(order, CancelReason.UNFILLED_IOC))
        else:
            self._book.add(order)
        return events

    def _new_market(self, command: NewMarketOrder) -> list[Event]:
        """Trade within the protection band, then cancel or rest what is left.

        Follows NSE's market price protection (circular 155/2022). The band is
        set from the last traded price when the order arrives. Whatever the
        order cannot fill inside it is cancelled if orders remain beyond the
        band, or if the order is IOC. Otherwise the other side of the book is
        empty, and a DAY order rests as a limit order at the best price on its
        own side, or at the last traded price if its own side is empty too.
        """
        order_id = self._issue_order_id()
        if command.quantity <= 0:
            return [OrderRejected(order_id=order_id, reason=RejectReason.INVALID_QUANTITY)]
        if self._last_trade_price is None:
            return [OrderRejected(order_id=order_id, reason=RejectReason.NO_LAST_TRADE_PRICE)]
        events: list[Event] = [
            OrderAccepted(
                order_id=order_id,
                side=command.side,
                price=None,
                quantity=command.quantity,
            )
        ]
        limit = self._rules.protection_limit(command.side, self._last_trade_price)
        order = self._incoming(order_id, command.side, limit, command.quantity)
        self._sweep(events, order)
        if not order.remaining:
            return events
        if self._book.side(command.side.opposite).best() is not None:
            events.append(_cancelled(order, CancelReason.PRICE_PROTECTION))
        elif command.validity is Validity.IOC:
            events.append(_cancelled(order, CancelReason.UNFILLED_IOC))
        else:
            own_best = self._book.side(command.side).best()
            order.price = own_best.price if own_best is not None else self._last_trade_price
            self._book.add(order)
            events.append(
                MarketOrderConverted(
                    order_id=order_id,
                    price=order.price,
                    remaining=order.remaining,
                )
            )
        return events

    def _cancel(self, command: CancelOrder) -> list[Event]:
        order = self._book.get(command.order_id)
        if order is None:
            reason = self._why_not_resting(command.order_id)
            return [CancelRejected(order_id=command.order_id, reason=reason)]
        self._book.remove(order)
        return [_cancelled(order, CancelReason.REQUESTED)]

    def _modify(self, command: ModifyOrder) -> list[Event]:
        order = self._book.get(command.order_id)
        if order is None:
            reason = self._why_not_resting(command.order_id)
            return [ModifyRejected(order_id=command.order_id, reason=reason)]
        if command.quantity <= 0:
            return [ModifyRejected(order_id=order.order_id, reason=RejectReason.INVALID_QUANTITY)]
        if price_problem := self._price_problem(command.price):
            return [ModifyRejected(order_id=order.order_id, reason=price_problem)]

        remaining = command.quantity - order.filled
        if remaining <= 0:
            self._book.remove(order)
            return [_modified(command, remaining=0, kept_priority=False)]

        if command.price == order.price and remaining <= order.remaining:
            self._book.reduce(order, order.remaining - remaining)
            return [_modified(command, remaining=remaining, kept_priority=True)]

        # A new price or a larger size goes to the back of the queue, exactly as a
        # new order would, and trades first if the new price crosses the spread.
        self._book.remove(order)
        events: list[Event] = [_modified(command, remaining=remaining, kept_priority=False)]
        order.price = command.price
        order.remaining = remaining
        order.priority = self._sequence
        self._sweep(events, order)
        if order.remaining:
            self._book.add(order)
        return events

    def _sweep(self, events: list[Event], taker: Order) -> None:
        """Trade an incoming order against the opposite side of the book.

        Walks the opposite side from the best price, and each level from its
        oldest order, until the incoming order is filled or the next price is
        beyond the incoming order's price. Every trade is at the resting order's
        price and moves the last traded price. Resting orders that fill leave the
        book as they go. The incoming order's own quantities are updated in place.
        """
        opposite = self._book.side(taker.side.opposite)
        buying = taker.side is Side.BUY
        while taker.remaining:
            level = opposite.best()
            if level is None or (
                level.price > taker.price if buying else level.price < taker.price
            ):
                return
            while taker.remaining and (maker := level.head) is not None:
                traded = min(taker.remaining, maker.remaining)
                level.reduce(maker, traded)
                maker.filled += traded
                taker.remaining -= traded
                taker.filled += traded
                self._last_trade_price = level.price
                events.append(
                    Trade(
                        trade_id=self._next_trade_id,
                        price=level.price,
                        quantity=traded,
                        maker_order_id=maker.order_id,
                        taker_order_id=taker.order_id,
                        aggressor=taker.side,
                    )
                )
                self._next_trade_id += 1
                if not maker.remaining:
                    self._book.remove(maker)

    def _incoming(self, order_id: int, side: Side, price: int, quantity: int) -> Order:
        """A new order, not yet on the book, with this command's time priority."""
        return Order(
            order_id=order_id,
            side=side,
            price=price,
            remaining=quantity,
            priority=self._sequence,
        )

    def _price_problem(self, price: int) -> RejectReason | None:
        """Why a limit price is unacceptable, or None if it is fine."""
        if price <= 0:
            return RejectReason.INVALID_PRICE
        band = self._rules.price_band
        if band is not None and not band.contains(price):
            return RejectReason.PRICE_OUT_OF_BAND
        return None

    def _why_not_resting(self, order_id: int) -> RejectReason:
        """Tell an order that has left the book apart from one that never existed.

        IDs are issued in sequence, so any ID below the next one was issued at
        some point. That answer needs no record of finished orders, so memory
        does not grow with the number of orders ever seen.
        """
        if 0 < order_id < self._next_order_id:
            return RejectReason.TOO_LATE
        return RejectReason.UNKNOWN_ORDER

    def _issue_order_id(self) -> int:
        order_id = self._next_order_id
        self._next_order_id += 1
        return order_id


def _cancelled(order: Order, reason: CancelReason) -> OrderCancelled:
    return OrderCancelled(order_id=order.order_id, quantity=order.remaining, reason=reason)


def _modified(command: ModifyOrder, *, remaining: int, kept_priority: bool) -> OrderModified:
    return OrderModified(
        order_id=command.order_id,
        price=command.price,
        quantity=command.quantity,
        remaining=remaining,
        kept_priority=kept_priority,
    )


def _resting_orders(side: BookSide) -> tuple[RestingOrder, ...]:
    return tuple(
        RestingOrder(
            order_id=order.order_id,
            side=order.side,
            price=order.price,
            remaining=order.remaining,
            filled=order.filled,
            priority=order.priority,
        )
        for level in side
        for order in level
    )
