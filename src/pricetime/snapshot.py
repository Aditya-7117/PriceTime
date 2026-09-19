"""An immutable, comparable copy of the engine's full state."""

import hashlib
import json
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

    def digest(self) -> str:
        """A SHA-256 fingerprint of the full state, including queue order.

        Built from a canonical JSON encoding of plain integers and strings, so it
        is the same in any process, on any machine, under any hash seed. Two
        engines that should agree can compare one string instead of every order.
        """
        state = {
            "bids": [_fields(order) for order in self.bids],
            "asks": [_fields(order) for order in self.asks],
            "next_order_id": self.next_order_id,
            "next_trade_id": self.next_trade_id,
            "sequence": self.sequence,
        }
        canonical = json.dumps(state, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


def _fields(order: RestingOrder) -> list[int | str]:
    return [
        order.order_id,
        order.side.value,
        order.price,
        order.remaining,
        order.filled,
        order.priority,
    ]
