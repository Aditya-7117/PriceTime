"""Events: everything the matching engine reports back.

Processing one command returns the list of events it caused, in the order they
happened. Business outcomes such as a rejected order are events, not exceptions:
they are part of the engine's normal output and have to replay identically.
"""

import enum
from dataclasses import dataclass

from pricetime.orders import Side


class RejectReason(enum.Enum):
    """Why a command was refused."""

    INVALID_QUANTITY = "invalid_quantity"
    """Quantity was zero or negative."""

    INVALID_PRICE = "invalid_price"
    """Price was zero or negative."""

    PRICE_OUT_OF_BAND = "price_out_of_band"
    """Price was outside the day's price band."""

    UNKNOWN_ORDER = "unknown_order"
    """No order with this ID was ever issued."""

    TOO_LATE = "too_late"
    """The order was issued but is no longer resting: it filled, was cancelled or was rejected."""


class CancelReason(enum.Enum):
    """Why open quantity left the book without trading."""

    REQUESTED = "requested"
    """The owner asked for it."""

    NO_LIQUIDITY = "no_liquidity"
    """A market order ran out of orders to trade against."""


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderAccepted:
    """A new order passed validation and was assigned an ID. Price is None for market orders."""

    order_id: int
    side: Side
    price: int | None
    quantity: int


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderRejected:
    """A new order failed validation. It was assigned an ID but never reached the book."""

    order_id: int
    reason: RejectReason


@dataclass(frozen=True, slots=True, kw_only=True)
class Trade:
    """An incoming order (the taker) executed against a resting order (the maker).

    The price is always the maker's price, so any improvement goes to the taker.
    """

    trade_id: int
    price: int
    quantity: int
    maker_order_id: int
    taker_order_id: int
    aggressor: Side


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderCancelled:
    """Open quantity was removed from an order without trading."""

    order_id: int
    quantity: int
    reason: CancelReason


@dataclass(frozen=True, slots=True, kw_only=True)
class CancelRejected:
    """A cancel was refused. The order it named, if any, is unchanged."""

    order_id: int
    reason: RejectReason


@dataclass(frozen=True, slots=True, kw_only=True)
class OrderModified:
    """A modify was applied.

    `quantity` is the new total the owner asked for. `remaining` is what that
    leaves open once earlier fills are counted, before any trading the modify
    itself triggers; zero means the order is finished and has left the book.
    `kept_priority` says whether the order held its place in the queue.
    """

    order_id: int
    price: int
    quantity: int
    remaining: int
    kept_priority: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class ModifyRejected:
    """A modify was refused. The order it named, if any, is unchanged."""

    order_id: int
    reason: RejectReason


type Event = (
    OrderAccepted
    | OrderRejected
    | Trade
    | OrderCancelled
    | OrderModified
    | CancelRejected
    | ModifyRejected
)
