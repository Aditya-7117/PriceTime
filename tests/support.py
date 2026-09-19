"""Shorthand for building commands and reading engine state in tests."""

import random

from pricetime.commands import CancelOrder, Command, ModifyOrder, NewLimitOrder, NewMarketOrder
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


def random_commands(seed: int, count: int) -> list[Command]:
    """A long command sequence that is identical every time for a given seed.

    For tests that need scale rather than shrinking. Uses its own seeded
    generator, never the global one.
    """
    rng = random.Random(seed)
    commands: list[Command] = []
    issued = 0
    for _ in range(count):
        roll = rng.random()
        side = rng.choice((Side.BUY, Side.SELL))
        if roll < 0.6:
            price = rng.randint(95, 102) if side is Side.BUY else rng.randint(98, 105)
            commands.append(NewLimitOrder(side=side, price=price, quantity=rng.randint(1, 20)))
            issued += 1
        elif roll < 0.7:
            commands.append(NewMarketOrder(side=side, quantity=rng.randint(1, 20)))
            issued += 1
        elif roll < 0.85:
            commands.append(CancelOrder(order_id=rng.randint(1, issued + 1)))
        else:
            order_id = rng.randint(1, issued + 1)
            price, quantity = rng.randint(95, 105), rng.randint(1, 20)
            commands.append(ModifyOrder(order_id=order_id, price=price, quantity=quantity))
    return commands
