"""The matching engine: applies commands to the book under price-time priority."""

from typing import assert_never

from pricetime.book import BookSide, OrderBook
from pricetime.commands import CancelOrder, Command, ModifyOrder, NewLimitOrder, NewMarketOrder
from pricetime.events import (
    CancelReason,
    CancelRejected,
    Event,
    ModifyRejected,
    OrderAccepted,
    OrderCancelled,
    OrderModified,
    OrderRejected,
    RejectReason,
    Trade,
)
from pricetime.orders import Order, Side
from pricetime.snapshot import EngineSnapshot, RestingOrder


class MatchingEngine:
    """A single-instrument matching engine.

    Commands are processed one at a time, to completion, in the order they are
    given. Each call to `process` returns the events that command caused and
    calls nothing else, so no outside code can run, or re-enter the engine, in
    the middle of a match. The engine reads no clock and no randomness: time
    priority comes from the order of commands, so the same commands always
    produce the same events and the same book.
    """

    def __init__(self) -> None:
        self._book = OrderBook()
        self._next_order_id = 1
        self._next_trade_id = 1
        self._sequence = 0

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
        )

    def _new_limit(self, command: NewLimitOrder) -> list[Event]:
        order_id = self._issue_order_id()
        if command.quantity <= 0:
            return [OrderRejected(order_id=order_id, reason=RejectReason.INVALID_QUANTITY)]
        if command.price <= 0:
            return [OrderRejected(order_id=order_id, reason=RejectReason.INVALID_PRICE)]
        events: list[Event] = [
            OrderAccepted(
                order_id=order_id,
                side=command.side,
                price=command.price,
                quantity=command.quantity,
            )
        ]
        unfilled = self._sweep(events, order_id, command.side, command.price, command.quantity)
        if unfilled:
            self._book.add(
                Order(
                    order_id=order_id,
                    side=command.side,
                    price=command.price,
                    remaining=unfilled,
                    filled=command.quantity - unfilled,
                    priority=self._sequence,
                )
            )
        return events

    def _new_market(self, command: NewMarketOrder) -> list[Event]:
        order_id = self._issue_order_id()
        if command.quantity <= 0:
            return [OrderRejected(order_id=order_id, reason=RejectReason.INVALID_QUANTITY)]
        events: list[Event] = [
            OrderAccepted(
                order_id=order_id,
                side=command.side,
                price=None,
                quantity=command.quantity,
            )
        ]
        unfilled = self._sweep(events, order_id, command.side, None, command.quantity)
        if unfilled:
            events.append(
                OrderCancelled(
                    order_id=order_id,
                    quantity=unfilled,
                    reason=CancelReason.NO_LIQUIDITY,
                )
            )
        return events

    def _cancel(self, command: CancelOrder) -> list[Event]:
        order = self._book.get(command.order_id)
        if order is None:
            reason = self._why_not_resting(command.order_id)
            return [CancelRejected(order_id=command.order_id, reason=reason)]
        self._book.remove(order)
        return [
            OrderCancelled(
                order_id=order.order_id,
                quantity=order.remaining,
                reason=CancelReason.REQUESTED,
            )
        ]

    def _modify(self, command: ModifyOrder) -> list[Event]:
        order = self._book.get(command.order_id)
        if order is None:
            reason = self._why_not_resting(command.order_id)
            return [ModifyRejected(order_id=command.order_id, reason=reason)]
        if command.quantity <= 0:
            return [ModifyRejected(order_id=order.order_id, reason=RejectReason.INVALID_QUANTITY)]
        if command.price <= 0:
            return [ModifyRejected(order_id=order.order_id, reason=RejectReason.INVALID_PRICE)]

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
        unfilled = self._sweep(events, order.order_id, order.side, command.price, remaining)
        order.filled += remaining - unfilled
        if unfilled:
            order.price = command.price
            order.remaining = unfilled
            order.priority = self._sequence
            self._book.add(order)
        return events

    def _sweep(
        self,
        events: list[Event],
        taker_id: int,
        side: Side,
        limit: int | None,
        quantity: int,
    ) -> int:
        """Trade an incoming order against the opposite side of the book.

        Walks the opposite side from the best price, and each level from its
        oldest order, until the incoming order is filled or the next price is
        beyond its limit. A limit of None means any price. Makers that fill
        completely leave the book as they go. Returns the quantity left unfilled.
        """
        opposite = self._book.side(side.opposite)
        buying = side is Side.BUY
        while quantity:
            level = opposite.best()
            if level is None:
                break
            if limit is not None and (level.price > limit if buying else level.price < limit):
                break
            while quantity and (maker := level.head) is not None:
                traded = min(quantity, maker.remaining)
                level.reduce(maker, traded)
                maker.filled += traded
                quantity -= traded
                events.append(
                    Trade(
                        trade_id=self._next_trade_id,
                        price=level.price,
                        quantity=traded,
                        maker_order_id=maker.order_id,
                        taker_order_id=taker_id,
                        aggressor=side,
                    )
                )
                self._next_trade_id += 1
                if not maker.remaining:
                    self._book.remove(maker)
        return quantity

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
