"""A price level: the first-in, first-out queue of resting orders at one price."""

from collections.abc import Iterator

from pricetime.orders import Order


class PriceLevel:
    """Orders resting at one price, oldest first.

    A doubly linked list threaded through the orders themselves. Append, remove and
    reduce are all O(1). Iteration runs from the oldest order to the newest and must
    not be mixed with removal; the matcher walks the queue through `head` instead.
    """

    __slots__ = ("count", "head", "price", "tail", "total_quantity")

    def __init__(self, price: int) -> None:
        self.price = price
        self.head: Order | None = None
        self.tail: Order | None = None
        self.count = 0
        self.total_quantity = 0

    def __len__(self) -> int:
        return self.count

    def __iter__(self) -> Iterator[Order]:
        order = self.head
        while order is not None:
            yield order
            order = order.next

    @property
    def is_empty(self) -> bool:
        """True when no orders rest at this price."""
        return self.head is None

    def append(self, order: Order) -> None:
        """Add an order at the back of the queue."""
        order.prev = self.tail
        order.next = None
        if self.tail is None:
            self.head = order
        else:
            self.tail.next = order
        self.tail = order
        self.count += 1
        self.total_quantity += order.remaining

    def remove(self, order: Order) -> None:
        """Unlink an order from anywhere in the queue."""
        if order.prev is None:
            self.head = order.next
        else:
            order.prev.next = order.next
        if order.next is None:
            self.tail = order.prev
        else:
            order.next.prev = order.prev
        order.prev = None
        order.next = None
        self.count -= 1
        self.total_quantity -= order.remaining

    def reduce(self, order: Order, quantity: int) -> None:
        """Take quantity off an order in place, leaving its queue position alone."""
        order.remaining -= quantity
        self.total_quantity -= quantity
