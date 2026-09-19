"""Conversion between decimal prices, as they arrive on the wire, and integer ticks.

The engine only ever sees whole numbers of ticks. This is the one place a decimal
price string becomes an integer, using exact decimal arithmetic, so a binary float
never exists anywhere in the system. A price that is not an exact multiple of the
instrument's tick size is refused here, before it can reach the engine.
"""

import re
from decimal import Decimal, InvalidOperation

_PLAIN_DECIMAL = re.compile(r"\d+(\.\d+)?")


class PriceError(ValueError):
    """A price string is malformed or does not sit on the tick grid."""


def to_ticks(price: str, tick_size: Decimal) -> int:
    """Convert a decimal price string to a whole number of ticks.

    Args:
        price: Digits with an optional fractional part, such as "2450.35".
        tick_size: The instrument's smallest price step, such as Decimal("0.05").

    Raises:
        PriceError: If the string is not a plain decimal, or the price is not an
            exact multiple of the tick size.
        ValueError: If the tick size is not positive.
    """
    _check_tick_size(tick_size)
    if not _PLAIN_DECIMAL.fullmatch(price):
        raise PriceError(f"{price!r} is not a plain decimal price")
    try:
        ticks, remainder = divmod(Decimal(price), tick_size)
    except InvalidOperation as error:
        raise PriceError(f"{price!r} is too large to convert exactly") from error
    if remainder:
        raise PriceError(f"{price} is not a multiple of the tick size {tick_size}")
    return int(ticks)


def from_ticks(ticks: int, tick_size: Decimal) -> Decimal:
    """Convert a whole number of ticks back to its exact decimal price."""
    _check_tick_size(tick_size)
    return ticks * tick_size


def _check_tick_size(tick_size: Decimal) -> None:
    if tick_size <= 0:
        raise ValueError(f"tick size must be positive, got {tick_size}")
