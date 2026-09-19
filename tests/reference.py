"""A deliberately naive order book, written from the rules rather than from the engine.

No linked lists, no ID index, no sorted price levels. Every resting order sits in
one flat list, and every step of a match sorts the other side from scratch and
takes the first order. It is far too slow for real use and simple enough to check
by eye, which is the point: the differential test requires the real engine to
produce exactly the same events and state as this, for any command sequence.

It shares the engine's vocabulary (commands, events, snapshot types) and nothing
else.
"""

from dataclasses import dataclass

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
from pricetime.orders import SelfTradeAction, Side
from pricetime.rules import MarketRules
from pricetime.snapshot import EngineSnapshot, RestingOrder


@dataclass(eq=False)
class _Order:
    order_id: int
    side: Side
    price: int
    remaining: int
    filled: int
    priority: int
    client_id: int
    self_trade: SelfTradeAction


class ReferenceBook:
    """The matching rules, implemented as plainly as possible."""

    def __init__(self, rules: MarketRules) -> None:
        self.rules = rules
        self.orders: list[_Order] = []
        self.next_order_id = 1
        self.next_trade_id = 1
        self.sequence = 0
        self.last_trade_price = rules.opening_price

    def process(self, command: Command) -> list[Event]:
        self.sequence += 1
        if isinstance(command, NewLimitOrder):
            return self._new_limit(command)
        if isinstance(command, NewMarketOrder):
            return self._new_market(command)
        if isinstance(command, CancelOrder):
            return self._cancel(command)
        return self._modify(command)

    def snapshot(self) -> EngineSnapshot:
        return EngineSnapshot(
            bids=tuple(_resting(o) for o in self._queue(Side.BUY)),
            asks=tuple(_resting(o) for o in self._queue(Side.SELL)),
            next_order_id=self.next_order_id,
            next_trade_id=self.next_trade_id,
            sequence=self.sequence,
            last_trade_price=self.last_trade_price,
        )

    def _queue(self, side: Side) -> list[_Order]:
        """One side's resting orders, best price first, then earliest first."""
        orders = [o for o in self.orders if o.side is side]
        if side is Side.BUY:
            return sorted(orders, key=lambda o: (-o.price, o.priority))
        return sorted(orders, key=lambda o: (o.price, o.priority))

    def _find(self, order_id: int) -> _Order | None:
        for order in self.orders:
            if order.order_id == order_id:
                return order
        return None

    def _issue(self) -> int:
        self.next_order_id += 1
        return self.next_order_id - 1

    def _not_resting(self, order_id: int) -> RejectReason:
        if 1 <= order_id < self.next_order_id:
            return RejectReason.TOO_LATE
        return RejectReason.UNKNOWN_ORDER

    def _bad_price(self, price: int) -> RejectReason | None:
        if price < 1:
            return RejectReason.INVALID_PRICE
        band = self.rules.price_band
        if band is not None and (price < band.lower or price > band.upper):
            return RejectReason.PRICE_OUT_OF_BAND
        return None

    def _match(self, taker: _Order, events: list[Event]) -> bool:
        """Trade the taker. True means it met its own client under cancel active."""
        while taker.remaining > 0:
            queue = self._queue(taker.side.opposite)
            if not queue:
                return False
            maker = queue[0]
            if taker.side is Side.BUY and maker.price > taker.price:
                return False
            if taker.side is Side.SELL and maker.price < taker.price:
                return False
            if maker.client_id == taker.client_id:
                if taker.self_trade is SelfTradeAction.CANCEL_ACTIVE:
                    return True
                self.orders.remove(maker)
                events.append(
                    OrderCancelled(
                        order_id=maker.order_id,
                        quantity=maker.remaining,
                        reason=CancelReason.SELF_TRADE,
                    )
                )
                continue
            quantity = min(taker.remaining, maker.remaining)
            maker.remaining -= quantity
            maker.filled += quantity
            taker.remaining -= quantity
            taker.filled += quantity
            self.last_trade_price = maker.price
            events.append(
                Trade(
                    trade_id=self.next_trade_id,
                    price=maker.price,
                    quantity=quantity,
                    maker_order_id=maker.order_id,
                    taker_order_id=taker.order_id,
                    aggressor=taker.side,
                )
            )
            self.next_trade_id += 1
            if maker.remaining == 0:
                self.orders.remove(maker)
        return False

    def _new_limit(self, command: NewLimitOrder) -> list[Event]:
        order_id = self._issue()
        if command.quantity < 1:
            return [OrderRejected(order_id=order_id, reason=RejectReason.INVALID_QUANTITY)]
        if (reason := self._bad_price(command.price)) is not None:
            return [OrderRejected(order_id=order_id, reason=reason)]
        events: list[Event] = [
            OrderAccepted(
                order_id=order_id,
                side=command.side,
                price=command.price,
                quantity=command.quantity,
            )
        ]
        taker = self._taker(order_id, command, command.price)
        stopped = self._match(taker, events)
        if taker.remaining > 0:
            if stopped:
                events.append(_cancel_event(taker, CancelReason.SELF_TRADE))
            elif command.validity is Validity.IOC:
                events.append(_cancel_event(taker, CancelReason.UNFILLED_IOC))
            else:
                self.orders.append(taker)
        return events

    def _new_market(self, command: NewMarketOrder) -> list[Event]:
        order_id = self._issue()
        if command.quantity < 1:
            return [OrderRejected(order_id=order_id, reason=RejectReason.INVALID_QUANTITY)]
        if self.last_trade_price is None:
            return [OrderRejected(order_id=order_id, reason=RejectReason.NO_LAST_TRADE_PRICE)]
        events: list[Event] = [
            OrderAccepted(
                order_id=order_id,
                side=command.side,
                price=None,
                quantity=command.quantity,
            )
        ]
        reference = self.last_trade_price
        width = max(
            reference * self.rules.protection_bps // 10_000, self.rules.protection_min_ticks
        )
        limit = reference + width if command.side is Side.BUY else reference - width
        taker = self._taker(order_id, command, limit)
        stopped = self._match(taker, events)
        if taker.remaining > 0:
            if stopped:
                events.append(_cancel_event(taker, CancelReason.SELF_TRADE))
            elif self._queue(command.side.opposite):
                events.append(_cancel_event(taker, CancelReason.PRICE_PROTECTION))
            elif command.validity is Validity.IOC:
                events.append(_cancel_event(taker, CancelReason.UNFILLED_IOC))
            else:
                own = self._queue(command.side)
                taker.price = own[0].price if own else self.last_trade_price
                self.orders.append(taker)
                events.append(
                    MarketOrderConverted(
                        order_id=order_id, price=taker.price, remaining=taker.remaining
                    )
                )
        return events

    def _cancel(self, command: CancelOrder) -> list[Event]:
        order = self._find(command.order_id)
        if order is None:
            reason = self._not_resting(command.order_id)
            return [CancelRejected(order_id=command.order_id, reason=reason)]
        self.orders.remove(order)
        return [_cancel_event(order, CancelReason.REQUESTED)]

    def _modify(self, command: ModifyOrder) -> list[Event]:
        order = self._find(command.order_id)
        if order is None:
            reason = self._not_resting(command.order_id)
            return [ModifyRejected(order_id=command.order_id, reason=reason)]
        if command.quantity < 1:
            return [ModifyRejected(order_id=order.order_id, reason=RejectReason.INVALID_QUANTITY)]
        if (problem := self._bad_price(command.price)) is not None:
            return [ModifyRejected(order_id=order.order_id, reason=problem)]

        def modified(remaining: int, kept: bool) -> OrderModified:
            return OrderModified(
                order_id=order.order_id,
                price=command.price,
                quantity=command.quantity,
                remaining=remaining,
                kept_priority=kept,
            )

        open_quantity = command.quantity - order.filled
        if open_quantity < 1:
            self.orders.remove(order)
            return [modified(0, kept=False)]
        if command.price == order.price and open_quantity <= order.remaining:
            order.remaining = open_quantity
            return [modified(open_quantity, kept=True)]
        self.orders.remove(order)
        events: list[Event] = [modified(open_quantity, kept=False)]
        order.price = command.price
        order.remaining = open_quantity
        order.priority = self.sequence
        stopped = self._match(order, events)
        if stopped:
            events.append(_cancel_event(order, CancelReason.SELF_TRADE))
        elif order.remaining > 0:
            self.orders.append(order)
        return events

    def _taker(self, order_id: int, command: NewLimitOrder | NewMarketOrder, price: int) -> _Order:
        return _Order(
            order_id=order_id,
            side=command.side,
            price=price,
            remaining=command.quantity,
            filled=0,
            priority=self.sequence,
            client_id=command.client_id,
            self_trade=command.self_trade,
        )


def _cancel_event(order: _Order, reason: CancelReason) -> OrderCancelled:
    return OrderCancelled(order_id=order.order_id, quantity=order.remaining, reason=reason)


def _resting(order: _Order) -> RestingOrder:
    return RestingOrder(
        order_id=order.order_id,
        side=order.side,
        price=order.price,
        remaining=order.remaining,
        filled=order.filled,
        priority=order.priority,
        client_id=order.client_id,
        self_trade=order.self_trade,
    )
