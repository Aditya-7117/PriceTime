import pytest

from pricetime.rules import PriceBand


@pytest.mark.parametrize(("lower", "upper"), [(0, 10), (-5, 10), (11, 10)])
def test_a_band_must_be_positive_and_ordered(lower: int, upper: int) -> None:
    with pytest.raises(ValueError, match="price band"):
        PriceBand(lower=lower, upper=upper)


@pytest.mark.parametrize(("price", "inside"), [(89, False), (90, True), (100, True), (110, True)])
def test_band_limits_are_inclusive(price: int, inside: bool) -> None:
    assert PriceBand(lower=90, upper=110).contains(price) is inside


def test_a_single_price_band_is_allowed() -> None:
    assert PriceBand(lower=100, upper=100).contains(100)
