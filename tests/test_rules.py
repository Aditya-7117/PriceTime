import pytest

from pricetime.orders import Side
from pricetime.rules import MarketRules, PriceBand


@pytest.mark.parametrize(("lower", "upper"), [(0, 10), (-5, 10), (11, 10)])
def test_a_band_must_be_positive_and_ordered(lower: int, upper: int) -> None:
    with pytest.raises(ValueError, match="price band"):
        PriceBand(lower=lower, upper=upper)


@pytest.mark.parametrize(("price", "inside"), [(89, False), (90, True), (100, True), (110, True)])
def test_band_limits_are_inclusive(price: int, inside: bool) -> None:
    assert PriceBand(lower=90, upper=110).contains(price) is inside


def test_a_single_price_band_is_allowed() -> None:
    assert PriceBand(lower=100, upper=100).contains(100)


@pytest.mark.parametrize(
    ("side", "last_trade_price", "limit"),
    [
        (Side.BUY, 100, 105),
        (Side.SELL, 100, 95),
        (Side.BUY, 99, 103),  # 4.95 ticks rounds down to 4: the band never exceeds X%
        (Side.BUY, 10, 12),  # 0.5 ticks is below the 2-tick minimum width
        (Side.SELL, 10, 8),
    ],
)
def test_market_orders_may_trade_up_to_x_percent_from_the_last_trade(
    side: Side, last_trade_price: int, limit: int
) -> None:
    rules = MarketRules(protection_bps=500, protection_min_ticks=2)

    assert rules.protection_limit(side, last_trade_price) == limit


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"protection_bps": -1}, "protection"),
        ({"protection_min_ticks": -1}, "protection"),
        ({"opening_price": 0}, "opening price"),
        ({"opening_price": 111}, "opening price"),
    ],
)
def test_rules_refuse_impossible_settings(changes: dict[str, int], problem: str) -> None:
    settings = {"protection_bps": 500, "protection_min_ticks": 2} | changes
    with pytest.raises(ValueError, match=problem):
        MarketRules(price_band=PriceBand(lower=90, upper=110), **settings)
