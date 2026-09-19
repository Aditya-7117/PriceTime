"""Commands: the only inputs the matching engine accepts.

Commands are plain immutable values. The engine's state is a function of the
sequence of commands it has processed and nothing else, which is what makes a
recorded sequence replayable.
"""

from dataclasses import dataclass

from pricetime.orders import Side


@dataclass(frozen=True, slots=True, kw_only=True)
class NewLimitOrder:
    """Buy or sell up to `quantity` at `price` or better; rest whatever does not fill."""

    side: Side
    price: int
    quantity: int


@dataclass(frozen=True, slots=True, kw_only=True)
class NewMarketOrder:
    """Buy or sell `quantity` at any price; cancel whatever the book cannot fill."""

    side: Side
    quantity: int


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
