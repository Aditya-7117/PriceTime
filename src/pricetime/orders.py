"""Order sides and the resting order record."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Side(enum.Enum):
    """Which side of the book an order sits on."""

    BUY = "buy"
    SELL = "sell"

    @property
    def opposite(self) -> Side:
        """The side this order trades against."""
        return Side.SELL if self is Side.BUY else Side.BUY


@dataclass(slots=True, eq=False, kw_only=True)
class Order:
    """A resting order, which is also its own node in its price level's queue.

    The queue links live on the order itself (an intrusive list). An order found
    through the ID index can therefore be unlinked in O(1), with no search and no
    separate node object.

    Prices are integer ticks and quantities are whole units. Equality is identity:
    two orders are the same only if they are the same object.

    Attributes:
        order_id: Engine-assigned identifier, unique for the life of the engine.
        side: Buy or sell.
        price: Limit price in ticks.
        remaining: Quantity still open on the book.
        priority: Engine sequence number at which the order took its current
            queue position. Lower means earlier.
        filled: Quantity executed so far.
        prev: The order ahead of this one in the queue.
        next: The order behind this one in the queue.
    """

    order_id: int
    side: Side
    price: int
    remaining: int
    priority: int
    filled: int = 0
    prev: Order | None = field(default=None, repr=False)
    next: Order | None = field(default=None, repr=False)
