"""What the exchange tells a client about its orders.

An execution report answers the same questions every time: which order, what
just happened to it, how much has traded in total, how much is still open, and
at what average price. The gateway keeps one view per order because the engine
does not: the engine works in ticks and order IDs, while a client speaks in its
own order IDs, decimal prices and account codes.
"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from pricetime.fix import tags
from pricetime.fix.session import fix_time
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
)
from pricetime.fix.wire import Fields
from pricetime.orders import Side
from pricetime.prices import from_ticks

EXTRA_AVERAGE_PLACES = 2


def average_price_places(tick_size: Decimal) -> Decimal:
    """The step an average price is rounded to: two places finer than the tick."""
    exponent = tick_size.normalize().as_tuple().exponent
    return Decimal(1).scaleb(min(int(exponent), 0) - EXTRA_AVERAGE_PLACES)


@dataclass(slots=True)
class OrderView:
    """What the gateway remembers about one order so it can report on it."""

    symbol: str
    order_id: int
    session: str
    account: str
    client_order_id: str
    side: Side
    order_type: OrdType
    price: int | None
    quantity: int
    leaves: int
    filled: int = 0
    traded_ticks: int = 0
    live: bool = True

    @property
    def exchange_order_id(self) -> str:
        """The exchange's own order ID, unique across instruments.

        An order refused before any engine saw it never got one, and FIX writes
        that as NONE.
        """
        return f"{self.symbol}:{self.order_id}" if self.order_id else "NONE"

    def fill(self, quantity: int, price: int) -> None:
        """Record a trade against this order."""
        self.filled += quantity
        self.leaves -= quantity
        self.traded_ticks += quantity * price
        if self.leaves == 0:
            self.live = False

    def average_price(self, tick_size: Decimal) -> Decimal:
        """The average price of everything traded so far, or zero before the first trade.

        Carried to two more decimal places than the tick, because an average of
        several fills rarely lands on the tick grid, and written plainly: FIX
        prices never use exponents.
        """
        places = average_price_places(tick_size)
        if self.filled == 0:
            return Decimal(0).quantize(places)
        average = Decimal(self.traded_ticks) / Decimal(self.filled) * tick_size
        return average.quantize(places)

    def status(self) -> OrdStatus:
        """Where this order stands, in the client's language."""
        if self.leaves > 0:
            return OrdStatus.PARTIALLY_FILLED if self.filled else OrdStatus.NEW
        return OrdStatus.FILLED if self.filled else OrdStatus.CANCELED


@dataclass(frozen=True, slots=True)
class Execution:
    """One execution report waiting to be written out."""

    view: OrderView
    exec_type: ExecType
    exec_id: str
    ord_status: OrdStatus | None = None
    client_order_id: str | None = None
    original_client_order_id: str | None = None
    last_quantity: int | None = None
    last_price: int | None = None
    text: str | None = None
    reject_reason: OrdRejReason | None = None
    restatement: ExecRestatementReason | None = None


def execution_report(execution: Execution, tick_size: Decimal, now: datetime) -> Fields:
    """Write an execution report for a client."""
    view = execution.view
    fields: list[tuple[int, str]] = [
        (tags.MSG_TYPE, MsgType.EXECUTION_REPORT),
        (tags.ORDER_ID, view.exchange_order_id),
        (tags.EXEC_ID, execution.exec_id),
        (tags.EXEC_TYPE, execution.exec_type),
        (tags.ORD_STATUS, execution.ord_status or view.status()),
        (tags.CL_ORD_ID, execution.client_order_id or view.client_order_id),
    ]
    if execution.original_client_order_id is not None:
        fields.append((tags.ORIG_CL_ORD_ID, execution.original_client_order_id))
    fields.extend(
        [
            (tags.ACCOUNT, view.account),
            (tags.SYMBOL, view.symbol),
            (tags.SIDE, fix_side(view.side)),
            (tags.ORDER_QTY, str(view.quantity)),
            (tags.ORD_TYPE, view.order_type),
        ]
    )
    if view.price is not None:
        fields.append((tags.PRICE, str(from_ticks(view.price, tick_size))))
    if execution.last_quantity is not None and execution.last_price is not None:
        fields.append((tags.LAST_QTY, str(execution.last_quantity)))
        fields.append((tags.LAST_PX, str(from_ticks(execution.last_price, tick_size))))
    fields.extend(
        [
            (tags.LEAVES_QTY, str(view.leaves)),
            (tags.CUM_QTY, str(view.filled)),
            (tags.AVG_PX, str(view.average_price(tick_size))),
            (tags.TRANSACT_TIME, fix_time(now)),
        ]
    )
    if execution.reject_reason is not None:
        fields.append((tags.ORD_REJ_REASON, execution.reject_reason))
    if execution.restatement is not None:
        fields.append((tags.EXEC_RESTATEMENT_REASON, execution.restatement))
    if execution.text is not None:
        fields.append((tags.TEXT, execution.text))
    return tuple(fields)


@dataclass(frozen=True, slots=True)
class Refusal:
    """A cancel or a replace the exchange will not act on, and why."""

    order_id: str
    client_order_id: str
    original_client_order_id: str
    ord_status: OrdStatus
    responding_to: CxlRejResponseTo
    reason: CxlRejReason
    text: str


def cancel_reject(refusal: Refusal, now: datetime) -> Fields:
    """Refuse a cancel or a replace, saying which request and why."""
    return (
        (tags.MSG_TYPE, MsgType.ORDER_CANCEL_REJECT),
        (tags.ORDER_ID, refusal.order_id),
        (tags.CL_ORD_ID, refusal.client_order_id),
        (tags.ORIG_CL_ORD_ID, refusal.original_client_order_id),
        (tags.ORD_STATUS, refusal.ord_status),
        (tags.CXL_REJ_RESPONSE_TO, refusal.responding_to),
        (tags.CXL_REJ_REASON, refusal.reason),
        (tags.TRANSACT_TIME, fix_time(now)),
        (tags.TEXT, refusal.text),
    )


def fix_side(side: Side) -> FixSide:
    """The engine's side, in the client's language."""
    return FixSide.BUY if side is Side.BUY else FixSide.SELL
