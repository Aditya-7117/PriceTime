"""The two sides of the order book and the index from order ID to resting order."""

import bisect
from collections.abc import Iterator

from pricetime.level import PriceLevel
from pricetime.orders import Order, Side


class BookSide:
    """The price levels on one side of the book, kept in priority order.

    Level prices sit in a sorted list of keys arranged so the best price is always
    last: the key is the price for bids and the negated price for asks. Reading the
    best level, and dropping it once a sweep empties it, both happen at the end of
    the list, which is O(1). A new price costs a binary search plus a shift of the
    keys behind it, and new prices mostly arrive near the top of the book, which is
    the cheap end.
    """

    __slots__ = ("_keys", "_levels", "_sign", "side")

    def __init__(self, side: Side) -> None:
        self.side = side
        self._sign = 1 if side is Side.BUY else -1
        self._keys: list[int] = []
        self._levels: dict[int, PriceLevel] = {}

    def __len__(self) -> int:
        return len(self._keys)

    def __iter__(self) -> Iterator[PriceLevel]:
        """Yield levels from the best price to the worst."""
        for key in reversed(self._keys):
            yield self._levels[key * self._sign]

    def best(self) -> PriceLevel | None:
        """The level at the best price, or None if this side is empty."""
        if not self._keys:
            return None
        return self._levels[self._keys[-1] * self._sign]

    def add(self, order: Order) -> None:
        """Queue an order at the back of its price level, creating the level if needed."""
        level = self._levels.get(order.price)
        if level is None:
            level = PriceLevel(order.price)
            self._levels[order.price] = level
            bisect.insort(self._keys, order.price * self._sign)
        level.append(order)

    def reduce(self, order: Order, quantity: int) -> None:
        """Take quantity off a resting order, leaving its queue position alone."""
        self._levels[order.price].reduce(order, quantity)

    def remove(self, order: Order) -> None:
        """Unlink a resting order, dropping its level if the level is now empty."""
        level = self._levels[order.price]
        level.remove(order)
        if level.is_empty:
            del self._levels[order.price]
            del self._keys[bisect.bisect_left(self._keys, order.price * self._sign)]


class OrderBook:
    """Resting orders on both sides, with an index from order ID to order.

    The book is a data structure only. It knows nothing about matching; the
    engine decides what rests, what trades and what leaves.
    """

    __slots__ = ("_orders", "asks", "bids")

    def __init__(self) -> None:
        self.bids = BookSide(Side.BUY)
        self.asks = BookSide(Side.SELL)
        self._orders: dict[int, Order] = {}

    def __len__(self) -> int:
        return len(self._orders)

    def __contains__(self, order_id: object) -> bool:
        return order_id in self._orders

    def side(self, side: Side) -> BookSide:
        """The bids for BUY, the asks for SELL."""
        return self.bids if side is Side.BUY else self.asks

    def get(self, order_id: int) -> Order | None:
        """The resting order with this ID, or None."""
        return self._orders.get(order_id)

    def best_bid(self) -> int | None:
        """The highest resting buy price, or None if there are no bids."""
        level = self.bids.best()
        return None if level is None else level.price

    def best_ask(self) -> int | None:
        """The lowest resting sell price, or None if there are no asks."""
        level = self.asks.best()
        return None if level is None else level.price

    def add(self, order: Order) -> None:
        """Rest an order at the back of its price level.

        Raises:
            ValueError: If an order with the same ID is already resting.
        """
        if order.order_id in self._orders:
            raise ValueError(f"order {order.order_id} is already resting")
        self.side(order.side).add(order)
        self._orders[order.order_id] = order

    def reduce(self, order: Order, quantity: int) -> None:
        """Take quantity off a resting order, leaving its queue position alone."""
        self.side(order.side).reduce(order, quantity)

    def remove(self, order: Order) -> None:
        """Take a resting order off the book in O(1), plus a level cleanup if it empties."""
        self.side(order.side).remove(order)
        del self._orders[order.order_id]
