"""Shorthand for building commands and reading engine state in tests."""

from pricetime.commands import NewLimitOrder, NewMarketOrder
from pricetime.engine import MatchingEngine
from pricetime.orders import Side


def buy(price: int, quantity: int) -> NewLimitOrder:
    return NewLimitOrder(side=Side.BUY, price=price, quantity=quantity)


def sell(price: int, quantity: int) -> NewLimitOrder:
    return NewLimitOrder(side=Side.SELL, price=price, quantity=quantity)


def market_buy(quantity: int) -> NewMarketOrder:
    return NewMarketOrder(side=Side.BUY, quantity=quantity)


def market_sell(quantity: int) -> NewMarketOrder:
    return NewMarketOrder(side=Side.SELL, quantity=quantity)


def resting(engine: MatchingEngine, side: Side) -> list[tuple[int, int, int]]:
    """Return (price, order_id, remaining) for each resting order, best first."""
    snapshot = engine.snapshot()
    orders = snapshot.bids if side is Side.BUY else snapshot.asks
    return [(order.price, order.order_id, order.remaining) for order in orders]
