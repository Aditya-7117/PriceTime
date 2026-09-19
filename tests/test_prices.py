from decimal import Decimal

import pytest

from pricetime.prices import PriceError, from_ticks, to_ticks

NSE_TICK = Decimal("0.05")


@pytest.mark.parametrize(
    ("price", "tick_size", "ticks"),
    [
        ("2450.35", NSE_TICK, 49007),
        ("0.05", NSE_TICK, 1),
        ("100", NSE_TICK, 2000),
        ("100.00", NSE_TICK, 2000),
        ("249.99", Decimal("0.01"), 24999),
        ("1234.50", Decimal("0.10"), 12345),
    ],
)
def test_prices_on_the_tick_grid_convert_exactly(
    price: str, tick_size: Decimal, ticks: int
) -> None:
    assert to_ticks(price, tick_size) == ticks


@pytest.mark.parametrize("price", ["2450.37", "0.01", "100.051"])
def test_a_price_off_the_tick_grid_is_refused(price: str) -> None:
    with pytest.raises(PriceError, match="not a multiple of the tick size"):
        to_ticks(price, NSE_TICK)


@pytest.mark.parametrize("price", ["", "abc", "1e3", "NaN", "Infinity", " 100", "1,000"])
def test_anything_but_a_plain_decimal_is_refused(price: str) -> None:
    with pytest.raises(PriceError, match="not a plain decimal price"):
        to_ticks(price, NSE_TICK)


@pytest.mark.parametrize("tick_size", [Decimal(0), Decimal("-0.05")])
def test_tick_size_must_be_positive(tick_size: Decimal) -> None:
    with pytest.raises(ValueError, match="tick size must be positive"):
        to_ticks("100", tick_size)


def test_ticks_convert_back_to_the_exact_decimal_price() -> None:
    assert from_ticks(49007, NSE_TICK) == Decimal("2450.35")
    assert str(from_ticks(2000, NSE_TICK)) == "100.00"


def test_a_round_trip_through_ticks_returns_the_same_price() -> None:
    for ticks in (1, 7, 49007, 10**9):
        assert to_ticks(str(from_ticks(ticks, NSE_TICK)), NSE_TICK) == ticks


def test_a_price_too_large_to_divide_exactly_is_refused() -> None:
    with pytest.raises(PriceError, match="too large"):
        to_ticks("1" + "0" * 40, NSE_TICK)
