import pytest

from pricetime.level import PriceLevel
from pricetime.orders import Order, Side


def make_order(order_id: int, quantity: int = 10, price: int = 100) -> Order:
    return Order(
        order_id=order_id, side=Side.BUY, price=price, remaining=quantity, priority=order_id
    )


def ids(level: PriceLevel) -> list[int]:
    return [order.order_id for order in level]


def test_new_level_is_empty() -> None:
    level = PriceLevel(100)

    assert level.is_empty
    assert level.head is None
    assert ids(level) == []
    assert level.total_quantity == 0


def test_orders_iterate_in_arrival_order() -> None:
    level = PriceLevel(100)
    for order_id in (1, 2, 3):
        level.append(make_order(order_id))

    assert ids(level) == [1, 2, 3]
    assert level.head is not None
    assert level.head.order_id == 1


def test_totals_track_appended_quantity() -> None:
    level = PriceLevel(100)
    level.append(make_order(1, quantity=5))
    level.append(make_order(2, quantity=7))

    assert len(level) == 2
    assert level.total_quantity == 12


@pytest.mark.parametrize(
    ("removed", "expected"),
    [(1, [2, 3]), (2, [1, 3]), (3, [1, 2])],
    ids=["head", "middle", "tail"],
)
def test_remove_unlinks_one_order_and_keeps_the_rest_in_order(
    removed: int, expected: list[int]
) -> None:
    level = PriceLevel(100)
    orders = {order_id: make_order(order_id, quantity=order_id) for order_id in (1, 2, 3)}
    for order in orders.values():
        level.append(order)

    level.remove(orders[removed])

    assert ids(level) == expected
    assert len(level) == 2
    assert level.total_quantity == 6 - removed
    assert orders[removed].prev is None
    assert orders[removed].next is None


def test_appending_after_removing_the_tail_links_to_the_new_tail() -> None:
    level = PriceLevel(100)
    first, second = make_order(1), make_order(2)
    level.append(first)
    level.append(second)

    level.remove(second)
    level.append(make_order(3))

    assert ids(level) == [1, 3]


def test_removing_the_only_order_empties_the_level() -> None:
    level = PriceLevel(100)
    order = make_order(1)
    level.append(order)

    level.remove(order)

    assert level.is_empty
    assert level.head is None
    assert level.total_quantity == 0
    assert ids(level) == []


def test_reduce_lowers_order_and_level_quantity_without_moving_the_order() -> None:
    level = PriceLevel(100)
    first, second = make_order(1, quantity=10), make_order(2, quantity=10)
    level.append(first)
    level.append(second)

    level.reduce(first, 4)

    assert first.remaining == 6
    assert level.total_quantity == 16
    assert ids(level) == [1, 2]
