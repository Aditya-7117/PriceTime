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


class CancelReason(enum.Enum):
    """Why open quantity left the book without trading."""

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


type Event = OrderAccepted | OrderRejected | Trade | OrderCancelled
