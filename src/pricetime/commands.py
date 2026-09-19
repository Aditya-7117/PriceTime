"""Commands: the only inputs the matching engine accepts.

Commands are plain immutable values. The engine's state is a function of the
sequence of commands it has processed and nothing else, which is what makes a
recorded sequence replayable.
"""

import enum
from dataclasses import dataclass

from pricetime.orders import SelfTradeAction, Side


class Validity(enum.Enum):
    """How long a new order may wait for a counterparty (NSE's time conditions)."""

    DAY = "day"
    """Rest on the book until filled, cancelled or the session ends."""

    IOC = "ioc"
    """Immediate or cancel: trade what is possible now and cancel the rest."""


@dataclass(frozen=True, slots=True, kw_only=True)
class NewLimitOrder:
    """Buy or sell up to `quantity` at `price` or better.

    What does not fill at once rests on the book for a DAY order, and is
    cancelled for an IOC order. `client_id` identifies who the order belongs to,
    as NSE identifies a client by PAN, so the order never trades with the same
    client's orders; `self_trade` says what to cancel when it would.
    """

    side: Side
    price: int
    quantity: int
    client_id: int
    validity: Validity = Validity.DAY
    self_trade: SelfTradeAction = SelfTradeAction.CANCEL_ACTIVE


@dataclass(frozen=True, slots=True, kw_only=True)
class NewMarketOrder:
    """Buy or sell `quantity` at the best prices available, within the protection band.

    It trades no further from the last traded price than the market rules
    allow. What it cannot fill is cancelled if orders remain beyond the band, or
    if it is IOC. Otherwise a DAY market order rests as a limit order. Clients
    and self-trade prevention work as for a limit order.
    """

    side: Side
    quantity: int
    client_id: int
    validity: Validity = Validity.DAY
    self_trade: SelfTradeAction = SelfTradeAction.CANCEL_ACTIVE


@dataclass(frozen=True, slots=True, kw_only=True)
class CancelOrder:
    """Remove the open quantity of a resting order. Filled quantity is final."""

    order_id: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ModifyOrder:
    """Change the price and total quantity of a resting order.

    `quantity` is the new total, including anything already filled, as in a
    FIX 4.4 cancel/replace. If 4 of 10 have filled and the owner asks for 8,
    4 stay open. Reading the 8 as open quantity would let a modify that races a
    fill leave the owner with more than they ever asked for.
    """

    order_id: int
    price: int
    quantity: int


type Command = NewLimitOrder | NewMarketOrder | CancelOrder | ModifyOrder
