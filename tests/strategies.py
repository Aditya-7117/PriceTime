"""Hypothesis strategies that generate command sequences for the engine.

A naive generator mostly produces commands that bounce off: cancels for IDs
that were never issued, modifies that never keep priority, books that never get
deep. This one follows the IDs it has issued, the same way the engine does, so
that cancels and modifies usually name a real order. Buys and sells draw from
overlapping price bands, so books build depth and also cross. Invalid values
and unknown IDs still appear, just not often enough to crowd out the rest.
"""

from hypothesis import strategies as st

from pricetime.commands import (
    CancelOrder,
    Command,
    ModifyOrder,
    NewLimitOrder,
    NewMarketOrder,
    Validity,
)
from pricetime.orders import Side
from pricetime.rules import MarketRules, PriceBand

BUY_PRICES = st.integers(min_value=95, max_value=102)
SELL_PRICES = st.integers(min_value=98, max_value=105)
QUANTITIES = st.integers(min_value=1, max_value=20)
INVALID_VALUES = st.sampled_from([0, -1])
# Mostly DAY, so books build depth; IOC often enough to exercise its cancels.
VALIDITIES = st.sampled_from([Validity.DAY, Validity.DAY, Validity.DAY, Validity.IOC])

# A band that sometimes cuts through the generated prices, so both sides of it are exercised.
price_bands = st.builds(
    PriceBand,
    lower=st.integers(min_value=93, max_value=97),
    upper=st.integers(min_value=103, max_value=107),
)
# Narrow and wide protection bands, a minimum width that sometimes dominates, and sessions
# that open with or without a last traded price.
market_rules = st.builds(
    MarketRules,
    price_band=st.none() | price_bands,
    protection_bps=st.sampled_from([100, 300, 1_000]),
    protection_min_ticks=st.sampled_from([0, 1, 3]),
    opening_price=st.none() | st.integers(min_value=98, max_value=102),
)

# Weights by repetition. Shrinking moves towards the start of the list, so a
# failing sequence shrinks towards plain limit orders.
KINDS = st.sampled_from(
    ["limit"] * 8 + ["market", "invalid order", "cancel", "cancel", "cancel", "modify", "modify"]
)


def prices_for(side: Side) -> st.SearchStrategy[int]:
    return BUY_PRICES if side is Side.BUY else SELL_PRICES


@st.composite
def command_sequences(draw: st.DrawFn, max_size: int = 100) -> list[Command]:
    """Draw a list of commands whose cancels and modifies mostly target issued orders."""
    commands: list[Command] = []
    # The last side, price and total quantity asked for, per limit order issued.
    limits: dict[int, tuple[Side, int, int]] = {}
    next_order_id = 1

    for _ in range(draw(st.integers(min_value=0, max_value=max_size))):
        kind = draw(KINDS)
        if kind in {"cancel", "modify"}:
            # 0 and next_order_id were never issued; everything between was.
            target = draw(st.integers(min_value=0, max_value=next_order_id))
            if kind == "cancel":
                commands.append(CancelOrder(order_id=target))
            else:
                modify = draw(_modify(target, limits.get(target)))
                commands.append(modify)
                if target in limits and modify.price > 0 and modify.quantity > 0:
                    limits[target] = (limits[target][0], modify.price, modify.quantity)
            continue

        side = draw(st.sampled_from(Side))
        if kind == "limit":
            price, quantity, validity = draw(prices_for(side)), draw(QUANTITIES), draw(VALIDITIES)
            commands.append(
                NewLimitOrder(side=side, price=price, quantity=quantity, validity=validity)
            )
            limits[next_order_id] = (side, price, quantity)
        elif kind == "market":
            commands.append(
                NewMarketOrder(side=side, quantity=draw(QUANTITIES), validity=draw(VALIDITIES))
            )
        else:
            bad_price = draw(st.booleans())
            price = draw(INVALID_VALUES) if bad_price else draw(prices_for(side))
            quantity = draw(QUANTITIES) if bad_price else draw(INVALID_VALUES)
            commands.append(NewLimitOrder(side=side, price=price, quantity=quantity))
        next_order_id += 1
    return commands


def _modify(order_id: int, last: tuple[Side, int, int] | None) -> st.SearchStrategy[ModifyOrder]:
    """A modify that shrinks in place, moves or grows, or is invalid."""
    if last is None:
        side_prices = st.sampled_from(Side).flatmap(prices_for)
        return st.builds(
            ModifyOrder, order_id=st.just(order_id), price=side_prices, quantity=QUANTITIES
        )
    side, price, quantity = last
    same_price_smaller = st.builds(
        ModifyOrder,
        order_id=st.just(order_id),
        price=st.just(price),
        quantity=st.integers(min_value=1, max_value=quantity),
    )
    anywhere = st.builds(
        ModifyOrder, order_id=st.just(order_id), price=prices_for(side), quantity=QUANTITIES
    )
    invalid = st.builds(
        ModifyOrder, order_id=st.just(order_id), price=prices_for(side), quantity=INVALID_VALUES
    ) | st.builds(
        ModifyOrder, order_id=st.just(order_id), price=INVALID_VALUES, quantity=QUANTITIES
    )
    return st.one_of(same_price_smaller, anywhere, anywhere, invalid)
