"""The gateway between FIX clients and the matching engines.

One engine per instrument sits behind this. The gateway turns a client's words
into commands an engine understands, and the engine's events back into
execution reports: decimal prices into ticks and back, account codes into
client IDs, client order IDs into engine order IDs.

Nothing reaches an engine until the gateway has checked it. The account must be
registered, the symbol listed, the price on the tick grid, the quantity a
positive whole number, and the session within its order rate. Whatever the
engine does accept is journaled, with a note of whose order it is, before it is
applied, so a restart rebuilds the book and the gateway's view of it together.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from functools import partial
from typing import assert_never

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
from pricetime.fix import tags
from pricetime.fix.config import ExchangeConfig, Instrument
from pricetime.fix.reports import (
    Execution,
    OrderView,
    Refusal,
    cancel_reject,
    execution_report,
)
from pricetime.fix.tags import (
    CxlRejReason,
    CxlRejResponseTo,
    ExecRestatementReason,
    ExecType,
    FixSide,
    MsgType,
    OrdRejReason,
    OrdStatus,
    OrdType,
    SelfTradeInstruction,
    TimeInForce,
)
from pricetime.fix.wire import Fields
from pricetime.journal import JournaledEngine, JournalRecord
from pricetime.orders import SelfTradeAction, Side
from pricetime.prices import PriceError, to_ticks

UNKNOWN_ORDER_ID = "NONE"

_SIDES: Mapping[str, Side] = {FixSide.BUY: Side.BUY, FixSide.SELL: Side.SELL}
_VALIDITIES: Mapping[str, Validity] = {
    TimeInForce.DAY: Validity.DAY,
    TimeInForce.IMMEDIATE_OR_CANCEL: Validity.IOC,
}
_SELF_TRADE: Mapping[str, SelfTradeAction] = {
    SelfTradeInstruction.CANCEL_ACTIVE: SelfTradeAction.CANCEL_ACTIVE,
    SelfTradeInstruction.CANCEL_PASSIVE: SelfTradeAction.CANCEL_PASSIVE,
}
_REJECTIONS: Mapping[RejectReason, tuple[OrdRejReason, str]] = {
    RejectReason.INVALID_QUANTITY: (OrdRejReason.INCORRECT_QUANTITY, "quantity must be positive"),
    RejectReason.INVALID_PRICE: (OrdRejReason.OTHER, "price must be positive"),
    RejectReason.PRICE_OUT_OF_BAND: (OrdRejReason.OTHER, "price is outside the day's band"),
    RejectReason.NO_LAST_TRADE_PRICE: (
        OrdRejReason.OTHER,
        "no trade yet today, so a market order has no protection band",
    ),
}
_CANCEL_REJECTIONS: Mapping[RejectReason, CxlRejReason] = {
    RejectReason.TOO_LATE: CxlRejReason.TOO_LATE_TO_CANCEL,
    RejectReason.UNKNOWN_ORDER: CxlRejReason.UNKNOWN_ORDER,
    RejectReason.INVALID_QUANTITY: CxlRejReason.OTHER,
    RejectReason.INVALID_PRICE: CxlRejReason.OTHER,
    RejectReason.PRICE_OUT_OF_BAND: CxlRejReason.OTHER,
}
_UNSOLICITED: Mapping[CancelReason, str] = {
    CancelReason.SELF_TRADE: "self-trade prevention",
    CancelReason.UNFILLED_IOC: "immediate or cancel",
    CancelReason.PRICE_PROTECTION: "market price protection",
}


class _Refuse(Exception):  # noqa: N818
    """A request the gateway will not pass to an engine, and the reason to send back."""

    def __init__(self, reason: OrdRejReason, text: str) -> None:
        super().__init__(text)
        self.reason = reason
        self.text = text


@dataclass(frozen=True, slots=True)
class Outbound:
    """A message for one session."""

    session: str
    fields: Fields


@dataclass(frozen=True, slots=True)
class _Request:
    """What a client asked for, kept while its events are turned into reports."""

    session: str
    client_order_id: str
    account: str = ""
    original_client_order_id: str | None = None
    responding_to: CxlRejResponseTo = CxlRejResponseTo.CANCEL


@dataclass(slots=True)
class _TokenBucket:
    """An order rate limit: tokens refill steadily, each order spends one."""

    rate: float
    capacity: float
    tokens: float
    updated: datetime

    def take(self, now: datetime) -> bool:
        """Spend a token if there is one."""
        elapsed = max(0.0, (now - self.updated).total_seconds())
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        self.updated = now
        if self.tokens < 1:
            return False
        self.tokens -= 1
        return True


class Gateway:
    """Turns FIX requests into engine commands, and engine events into reports."""

    def __init__(self, config: ExchangeConfig) -> None:
        self._config = config
        self._views: dict[tuple[str, int], OrderView] = {}
        self._client_orders: dict[tuple[str, str], tuple[str, int]] = {}
        self._buckets: dict[str, _TokenBucket] = {}
        self._refusals = 0
        self._engines: dict[str, JournaledEngine] = {
            symbol: JournaledEngine(
                instrument.journal,
                instrument.rules,
                on_recovered=partial(self._recover, symbol),
            )
            for symbol, instrument in config.instruments.items()
        }

    def close(self) -> None:
        """Close every journal."""
        for engine in self._engines.values():
            engine.close()

    def commit(self) -> None:
        """Force every journal onto the disk, before anything is acknowledged."""
        for engine in self._engines.values():
            engine.sync()

    def handle(self, session: str, fields: Fields, now: datetime) -> list[Outbound]:
        """Act on one application message from a session."""
        message = dict(fields)
        match message.get(tags.MSG_TYPE):
            case MsgType.NEW_ORDER_SINGLE:
                return self._new_order(session, message, now)
            case MsgType.ORDER_CANCEL_REQUEST:
                return self._cancel_order(session, message, now)
            case MsgType.ORDER_CANCEL_REPLACE_REQUEST:
                return self._replace_order(session, message, now)
            case _:
                return [self._business_reject(session, message)]

    def disconnected(self, session: str, now: datetime) -> list[Outbound]:
        """A session's connection dropped: cancel its orders if it asked for that."""
        settings = self._config.sessions.get(session)
        if settings is None or not settings.cancel_on_disconnect:
            return []
        outbound: list[Outbound] = []
        for (symbol, order_id), view in list(self._views.items()):
            if not view.live or view.session != session:
                continue
            request = _Request(session=session, client_order_id=view.client_order_id)
            outbound.extend(
                self._apply(
                    symbol,
                    CancelOrder(order_id=order_id),
                    {"session": session, "reason": "cancel on disconnect"},
                    request,
                    now,
                )
            )
        return outbound

    def _new_order(self, session: str, message: Mapping[int, str], now: datetime) -> list[Outbound]:
        symbol = message.get(tags.SYMBOL, "")
        request = _Request(
            session=session,
            client_order_id=message.get(tags.CL_ORD_ID, ""),
            account=message.get(tags.ACCOUNT, ""),
        )
        try:
            command = self._order_command(session, message, request, now)
        except _Refuse as refusal:
            return [self._refuse_order(request, symbol, now, refusal.reason, refusal.text)]
        annotation = {
            "session": session,
            "clordid": request.client_order_id,
            "account": request.account,
        }
        return self._apply(symbol, command, annotation, request, now)

    def _order_command(
        self, session: str, message: Mapping[int, str], request: _Request, now: datetime
    ) -> Command:
        """Read a new order, or refuse it before any engine sees it.

        Raises:
            _Refuse: If anything about the order is unacceptable.
        """
        if (session, request.client_order_id) in self._client_orders:
            raise _Refuse(OrdRejReason.DUPLICATE_ORDER, "this ClOrdID has been used before")
        if not self._within_rate(session, now):
            raise _Refuse(OrdRejReason.OTHER, "order rate limit exceeded")
        symbol = message.get(tags.SYMBOL, "")
        instrument = self._config.instruments.get(symbol)
        if instrument is None:
            raise _Refuse(OrdRejReason.UNKNOWN_SYMBOL, f"{symbol!r} is not traded here")
        client_id = self._config.accounts.get(request.account)
        if client_id is None:
            raise _Refuse(
                OrdRejReason.UNKNOWN_ACCOUNT, f"account {request.account!r} is not registered"
            )

        side = _SIDES.get(message.get(tags.SIDE, ""))
        validity = _VALIDITIES.get(message.get(tags.TIME_IN_FORCE, TimeInForce.DAY))
        self_trade = _SELF_TRADE.get(
            message.get(tags.SELF_TRADE_INSTRUCTION, SelfTradeInstruction.CANCEL_ACTIVE)
        )
        if side is None or validity is None or self_trade is None:
            raise _Refuse(
                OrdRejReason.UNSUPPORTED_CHARACTERISTIC,
                "side, time in force or self-trade instruction is not one this exchange takes",
            )
        order_type = message.get(tags.ORD_TYPE, "")
        if order_type not in {OrdType.LIMIT, OrdType.MARKET}:
            raise _Refuse(
                OrdRejReason.UNSUPPORTED_CHARACTERISTIC, "only limit and market orders are taken"
            )
        quantity = _whole(message.get(tags.ORDER_QTY, ""))
        if quantity is None or quantity < 1:
            raise _Refuse(OrdRejReason.INCORRECT_QUANTITY, "quantity must be a positive number")

        if order_type == OrdType.MARKET:
            return NewMarketOrder(
                side=side,
                quantity=quantity,
                client_id=client_id,
                validity=validity,
                self_trade=self_trade,
            )
        price = _price(message.get(tags.PRICE, ""), instrument.tick_size)
        if price is None:
            raise _Refuse(OrdRejReason.OTHER, "price must sit on the instrument's tick grid")
        return NewLimitOrder(
            side=side,
            price=price,
            quantity=quantity,
            client_id=client_id,
            validity=validity,
            self_trade=self_trade,
        )

    def _cancel_order(
        self, session: str, message: Mapping[int, str], now: datetime
    ) -> list[Outbound]:
        client_order_id = message.get(tags.CL_ORD_ID, "")
        original = message.get(tags.ORIG_CL_ORD_ID, "")
        request = _Request(
            session=session,
            client_order_id=client_order_id,
            original_client_order_id=original,
            responding_to=CxlRejResponseTo.CANCEL,
        )
        located = self._client_orders.get((session, original))
        if located is None:
            return [self._refuse_request(request, CxlRejReason.UNKNOWN_ORDER, "no such order", now)]
        symbol, order_id = located
        annotation = {"session": session, "clordid": client_order_id, "orig": original}
        return self._apply(symbol, CancelOrder(order_id=order_id), annotation, request, now)

    def _replace_order(
        self, session: str, message: Mapping[int, str], now: datetime
    ) -> list[Outbound]:
        client_order_id = message.get(tags.CL_ORD_ID, "")
        original = message.get(tags.ORIG_CL_ORD_ID, "")
        request = _Request(
            session=session,
            client_order_id=client_order_id,
            original_client_order_id=original,
            responding_to=CxlRejResponseTo.REPLACE,
        )
        refuse = partial(self._refuse_request, request)
        located = self._client_orders.get((session, original))
        if located is None:
            return [refuse(CxlRejReason.UNKNOWN_ORDER, "no such order", now)]
        symbol, order_id = located
        instrument = self._config.instruments[symbol]

        quantity = _whole(message.get(tags.ORDER_QTY, ""))
        price = _price(message.get(tags.PRICE, ""), instrument.tick_size)
        if quantity is None or quantity < 1:
            return [refuse(CxlRejReason.OTHER, "quantity must be a positive number", now)]
        if price is None:
            return [refuse(CxlRejReason.OTHER, "a replacement needs a price on the tick grid", now)]

        annotation = {"session": session, "clordid": client_order_id, "orig": original}
        command = ModifyOrder(order_id=order_id, price=price, quantity=quantity)
        return self._apply(symbol, command, annotation, request, now)

    def _apply(
        self,
        symbol: str,
        command: Command,
        annotation: dict[str, str],
        request: _Request,
        now: datetime,
    ) -> list[Outbound]:
        """Journal a command, apply it, and turn what happened into reports."""
        engine = self._engines[symbol]
        events = engine.process(command, annotation)
        self._client_orders.setdefault((request.session, request.client_order_id), (symbol, 0))
        return self._report(symbol, engine.engine.sequence, events, request, now)

    def _recover(self, symbol: str, record: JournalRecord, events: list[Event]) -> None:
        """Rebuild the gateway's view of an order while its journal is replayed."""
        annotation = record.annotation or {}
        request = _Request(
            session=annotation.get("session", ""),
            client_order_id=annotation.get("clordid", ""),
            account=annotation.get("account", ""),
            original_client_order_id=annotation.get("orig"),
        )
        self._client_orders.setdefault((request.session, request.client_order_id), (symbol, 0))
        self._report(symbol, record.sequence, events, request, _RECOVERY_TIME)

    def _report(
        self,
        symbol: str,
        sequence: int,
        events: list[Event],
        request: _Request,
        now: datetime,
    ) -> list[Outbound]:
        instrument = self._config.instruments[symbol]
        outbound: list[Outbound] = []
        for index, event in enumerate(events):
            outbound.extend(
                self._report_event(instrument, f"{symbol}-{sequence}-{index}", event, request, now)
            )
        return outbound

    def _report_event(
        self,
        instrument: Instrument,
        exec_id: str,
        event: Event,
        request: _Request,
        now: datetime,
    ) -> list[Outbound]:
        outbound: list[Outbound]
        match event:
            case OrderAccepted():
                view = self._open(instrument.symbol, event, request)
                outbound = [
                    self._send(view, Execution(view, ExecType.NEW, exec_id), instrument, now)
                ]
            case OrderRejected():
                reason, text = _REJECTIONS[event.reason]
                view = self._phantom(instrument.symbol, request, order_id=event.order_id)
                outbound = [
                    self._send(
                        view,
                        Execution(
                            view,
                            ExecType.REJECTED,
                            exec_id,
                            ord_status=OrdStatus.REJECTED,
                            reject_reason=reason,
                            text=text,
                        ),
                        instrument,
                        now,
                    )
                ]
            case Trade():
                outbound = [
                    self._trade(instrument, f"{exec_id}-{role}", order_id, event, now)
                    for role, order_id in (
                        ("m", event.maker_order_id),
                        ("t", event.taker_order_id),
                    )
                ]
            case OrderCancelled():
                outbound = [self._cancelled(instrument, exec_id, event, request, now)]
            case OrderModified():
                outbound = [self._modified(instrument, exec_id, event, request, now)]
            case MarketOrderConverted():
                view = self._views[(instrument.symbol, event.order_id)]
                view.price = event.price
                view.order_type = OrdType.LIMIT
                outbound = [
                    self._send(
                        view,
                        Execution(
                            view,
                            ExecType.RESTATED,
                            exec_id,
                            restatement=ExecRestatementReason.REPRICING_OF_ORDER,
                            text="market order rested as a limit order",
                        ),
                        instrument,
                        now,
                    )
                ]
            case CancelRejected() | ModifyRejected():
                outbound = [
                    self._refuse_request(
                        request, _CANCEL_REJECTIONS[event.reason], event.reason.value, now
                    )
                ]
            case _:
                assert_never(event)
        return outbound

    def _trade(
        self, instrument: Instrument, exec_id: str, order_id: int, event: Trade, now: datetime
    ) -> Outbound:
        view = self._views[(instrument.symbol, order_id)]
        view.fill(event.quantity, event.price)
        execution = Execution(
            view,
            ExecType.TRADE,
            exec_id,
            last_quantity=event.quantity,
            last_price=event.price,
        )
        return self._send(view, execution, instrument, now)

    def _cancelled(
        self,
        instrument: Instrument,
        exec_id: str,
        event: OrderCancelled,
        request: _Request,
        now: datetime,
    ) -> Outbound:
        view = self._views[(instrument.symbol, event.order_id)]
        view.leaves = 0
        view.live = False
        unsolicited = _UNSOLICITED.get(event.reason)
        execution = Execution(
            view,
            ExecType.CANCELED,
            exec_id,
            ord_status=OrdStatus.CANCELED,
            client_order_id=view.client_order_id if unsolicited else request.client_order_id,
            original_client_order_id=None if unsolicited else view.client_order_id,
            restatement=ExecRestatementReason.MARKET_OPTION if unsolicited else None,
            text=unsolicited,
        )
        return self._send(view, execution, instrument, now)

    def _modified(
        self,
        instrument: Instrument,
        exec_id: str,
        event: OrderModified,
        request: _Request,
        now: datetime,
    ) -> Outbound:
        view = self._views[(instrument.symbol, event.order_id)]
        previous = view.client_order_id
        view.price = event.price
        view.quantity = event.quantity
        view.leaves = event.remaining
        view.live = event.remaining > 0
        view.client_order_id = request.client_order_id or previous
        self._client_orders[(view.session, view.client_order_id)] = (
            instrument.symbol,
            view.order_id,
        )
        execution = Execution(
            view,
            ExecType.REPLACED,
            exec_id,
            original_client_order_id=previous,
            text="kept its place in the queue"
            if event.kept_priority
            else "went to the back of the queue",
        )
        return self._send(view, execution, instrument, now)

    def _open(self, symbol: str, event: OrderAccepted, request: _Request) -> OrderView:
        view = OrderView(
            symbol=symbol,
            order_id=event.order_id,
            session=request.session,
            account=request.account,
            client_order_id=request.client_order_id,
            side=event.side,
            order_type=OrdType.LIMIT if event.price is not None else OrdType.MARKET,
            price=event.price,
            quantity=event.quantity,
            leaves=event.quantity,
        )
        self._views[(symbol, event.order_id)] = view
        self._client_orders[(request.session, request.client_order_id)] = (symbol, event.order_id)
        return view

    def _phantom(self, symbol: str, request: _Request, order_id: int = 0) -> OrderView:
        """A view for an order that never reached the book, so it can still be reported.

        An order the engine refused has an ID, because the engine issues one
        before it decides. One refused before that has none.
        """
        return OrderView(
            symbol=symbol,
            order_id=order_id,
            session=request.session,
            account=request.account,
            client_order_id=request.client_order_id,
            side=Side.BUY,
            order_type=OrdType.LIMIT,
            price=None,
            quantity=0,
            leaves=0,
            live=False,
        )

    def _send(
        self, view: OrderView, execution: Execution, instrument: Instrument, now: datetime
    ) -> Outbound:
        return Outbound(view.session, execution_report(execution, instrument.tick_size, now))

    def _refuse_order(
        self,
        request: _Request,
        symbol: str,
        now: datetime,
        reason: OrdRejReason,
        text: str,
    ) -> Outbound:
        """Refuse a new order before it reaches an engine."""
        self._refusals += 1
        view = self._phantom(symbol, request)
        execution = Execution(
            view,
            ExecType.REJECTED,
            f"{symbol or 'X'}-refused-{self._refusals}",
            ord_status=OrdStatus.REJECTED,
            reject_reason=reason,
            text=text,
        )
        return self._send(view, execution, self._instrument_or_default(symbol), now)

    def _refuse_request(
        self, request: _Request, reason: CxlRejReason, text: str, now: datetime
    ) -> Outbound:
        """Refuse a cancel or a replace."""
        located = self._client_orders.get((request.session, request.original_client_order_id or ""))
        view = self._views.get(located) if located else None
        refusal = Refusal(
            order_id=view.exchange_order_id if view else UNKNOWN_ORDER_ID,
            client_order_id=request.client_order_id,
            original_client_order_id=request.original_client_order_id or "",
            ord_status=view.status() if view else OrdStatus.REJECTED,
            responding_to=request.responding_to,
            reason=reason,
            text=text,
        )
        return Outbound(request.session, cancel_reject(refusal, now))

    def _business_reject(self, session: str, message: Mapping[int, str]) -> Outbound:
        """Say that a message type this exchange does not support was ignored."""
        return Outbound(
            session,
            (
                (tags.MSG_TYPE, MsgType.BUSINESS_MESSAGE_REJECT),
                (tags.REF_MSG_TYPE, message.get(tags.MSG_TYPE, "")),
                (tags.TEXT, "this exchange accepts new, cancel and replace requests only"),
            ),
        )

    def _within_rate(self, session: str, now: datetime) -> bool:
        settings = self._config.sessions.get(session)
        if settings is None or not settings.throttled:
            return True
        bucket = self._buckets.get(session)
        if bucket is None:
            bucket = _TokenBucket(
                rate=float(settings.orders_per_second),
                capacity=float(settings.burst or settings.orders_per_second),
                tokens=float(settings.burst or settings.orders_per_second),
                updated=now,
            )
            self._buckets[session] = bucket
        return bucket.take(now)

    def _instrument_or_default(self, symbol: str) -> Instrument:
        """The instrument, or any of them, when a refusal has no valid symbol to price with."""
        instrument = self._config.instruments.get(symbol)
        if instrument is not None:
            return instrument
        return next(iter(self._config.instruments.values()))


_RECOVERY_TIME = datetime.min


def _whole(value: str) -> int | None:
    """A positive whole number, or None if that is not what this is."""
    return int(value) if value.isdigit() else None


def _price(value: str, tick_size: Decimal) -> int | None:
    """A price in ticks, or None if it is missing or off the grid."""
    try:
        return to_ticks(value, tick_size)
    except PriceError:
        return None
