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


type Command = NewLimitOrder | NewMarketOrder | CancelOrder
