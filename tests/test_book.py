import pytest

from pricetime.book import OrderBook
from pricetime.orders import Order, Side


def make_order(order_id: int, side: Side, price: int, quantity: int = 10) -> Order:
    return Order(order_id=order_id, side=side, price=price, remaining=quantity, priority=order_id)


def level_prices(book: OrderBook, side: Side) -> list[int]:
    return [level.price for level in book.side(side)]


def test_empty_book_has_no_best_prices() -> None:
    book = OrderBook()

    assert book.best_bid() is None
    assert book.best_ask() is None
    assert len(book) == 0


def test_best_bid_is_the_highest_buy_and_best_ask_the_lowest_sell() -> None:
    book = OrderBook()
    for order_id, price in enumerate((99, 101, 100), start=1):
        book.add(make_order(order_id, Side.BUY, price))
    for order_id, price in enumerate((105, 103, 104), start=4):
        book.add(make_order(order_id, Side.SELL, price))

    assert book.best_bid() == 101
    assert book.best_ask() == 103
    assert len(book) == 6


def test_levels_iterate_from_best_to_worst_on_each_side() -> None:
    book = OrderBook()
    for order_id, price in enumerate((99, 101, 100), start=1):
        book.add(make_order(order_id, Side.BUY, price))
    for order_id, price in enumerate((105, 103, 104), start=4):
        book.add(make_order(order_id, Side.SELL, price))

    assert level_prices(book, Side.BUY) == [101, 100, 99]
    assert level_prices(book, Side.SELL) == [103, 104, 105]


def test_orders_at_one_price_share_a_level_in_arrival_order() -> None:
    book = OrderBook()
    for order_id in (1, 2, 3):
        book.add(make_order(order_id, Side.BUY, 100))

    (level,) = book.side(Side.BUY)
    assert [order.order_id for order in level] == [1, 2, 3]
    assert level.total_quantity == 30


def test_get_finds_resting_orders_by_id() -> None:
    book = OrderBook()
    order = make_order(7, Side.SELL, 100)
    book.add(order)

    assert book.get(7) is order
    assert 7 in book
    assert book.get(8) is None
    assert 8 not in book


def test_removing_the_last_order_at_a_price_removes_the_level() -> None:
    book = OrderBook()
    best = make_order(1, Side.BUY, 101)
    book.add(best)
    book.add(make_order(2, Side.BUY, 100))

    book.remove(best)

    assert book.best_bid() == 100
    assert level_prices(book, Side.BUY) == [100]
    assert book.get(1) is None
    assert len(book) == 1


def test_removing_one_of_several_orders_keeps_the_level() -> None:
    book = OrderBook()
    first, second = make_order(1, Side.SELL, 100), make_order(2, Side.SELL, 100)
    book.add(first)
    book.add(second)

    book.remove(first)

    (level,) = book.side(Side.SELL)
    assert [order.order_id for order in level] == [2]
    assert book.best_ask() == 100


def test_a_price_level_can_be_emptied_and_used_again() -> None:
    book = OrderBook()
    first = make_order(1, Side.SELL, 100)
    book.add(first)
    book.remove(first)

    book.add(make_order(2, Side.SELL, 100))

    assert level_prices(book, Side.SELL) == [100]
    assert book.best_ask() == 100


def test_adding_an_order_id_twice_is_an_error() -> None:
    book = OrderBook()
    book.add(make_order(1, Side.BUY, 100))

    with pytest.raises(ValueError, match="already resting"):
        book.add(make_order(1, Side.SELL, 101))
