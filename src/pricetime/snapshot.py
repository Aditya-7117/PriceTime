"""An immutable, comparable copy of the engine's full state."""

from dataclasses import dataclass

from pricetime.orders import Side


@dataclass(frozen=True, slots=True, kw_only=True)
class RestingOrder:
    """One order on the book, as it stood when the snapshot was taken."""

    order_id: int
    side: Side
    price: int
    remaining: int
    filled: int
    priority: int


@dataclass(frozen=True, slots=True, kw_only=True)
class EngineSnapshot:
    """Everything that determines how the engine will respond to its next command.

    Each side lists its orders in priority order: best price first, and within a
    price, earliest first. Two engines with equal snapshots respond identically
    to any future command, which is the property replay is checked against.
    """

    bids: tuple[RestingOrder, ...]
    asks: tuple[RestingOrder, ...]
    next_order_id: int
    next_trade_id: int
    sequence: int
