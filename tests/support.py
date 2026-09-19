"""Shorthand for building commands and reading engine state in tests."""

import random
from dataclasses import replace

from pricetime.commands import (
    CancelOrder,
    Command,
    ModifyOrder,
    NewLimitOrder,
    NewMarketOrder,
    Validity,
)
from pricetime.engine import MatchingEngine
from pricetime.orders import SelfTradeAction, Side
from pricetime.rules import MarketRules, PriceBand

# No price band, a last traded price of 100 so market orders are accepted at once, and a
# 10% protection band wide enough to stay out of the way of tests about something else.
DEFAULT_RULES = MarketRules(protection_bps=1_000, protection_min_ticks=1, opening_price=100)


def new_engine(rules: MarketRules = DEFAULT_RULES) -> MatchingEngine:
    return MatchingEngine(rules)


def banded(lower: int, upper: int) -> MarketRules:
    """The default rules with a daily price band."""
    return replace(DEFAULT_RULES, price_band=PriceBand(lower=lower, upper=upper))


# Buys and sells come from different clients unless a test says otherwise, so they trade.
BUYER = 1
SELLER = 2
ACTIVE = SelfTradeAction.CANCEL_ACTIVE


def buy(
    price: int,
    quantity: int,
    *,
    client: int = BUYER,
    validity: Validity = Validity.DAY,
    self_trade: SelfTradeAction = ACTIVE,
) -> NewLimitOrder:
    return NewLimitOrder(
        side=Side.BUY,
        price=price,
        quantity=quantity,
        client_id=client,
        validity=validity,
        self_trade=self_trade,
    )


def sell(
    price: int,
    quantity: int,
    *,
    client: int = SELLER,
    validity: Validity = Validity.DAY,
    self_trade: SelfTradeAction = ACTIVE,
) -> NewLimitOrder:
    return NewLimitOrder(
        side=Side.SELL,
        price=price,
        quantity=quantity,
        client_id=client,
        validity=validity,
        self_trade=self_trade,
    )


def market_buy(
    quantity: int, *, client: int = BUYER, validity: Validity = Validity.DAY
) -> NewMarketOrder:
    return NewMarketOrder(side=Side.BUY, quantity=quantity, client_id=client, validity=validity)


def market_sell(
    quantity: int, *, client: int = SELLER, validity: Validity = Validity.DAY
) -> NewMarketOrder:
    return NewMarketOrder(side=Side.SELL, quantity=quantity, client_id=client, validity=validity)


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
        client = rng.randint(1, 3)
        if roll < 0.6:
            price = rng.randint(95, 102) if side is Side.BUY else rng.randint(98, 105)
            quantity = rng.randint(1, 20)
            commands.append(
                NewLimitOrder(side=side, price=price, quantity=quantity, client_id=client)
            )
            issued += 1
        elif roll < 0.7:
            quantity = rng.randint(1, 20)
            commands.append(NewMarketOrder(side=side, quantity=quantity, client_id=client))
            issued += 1
        elif roll < 0.85:
            commands.append(CancelOrder(order_id=rng.randint(1, issued + 1)))
        else:
            order_id = rng.randint(1, issued + 1)
            price, quantity = rng.randint(95, 105), rng.randint(1, 20)
            commands.append(ModifyOrder(order_id=order_id, price=price, quantity=quantity))
    return commands
