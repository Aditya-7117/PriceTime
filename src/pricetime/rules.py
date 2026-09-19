"""The trading rules an engine enforces for its instrument during one session."""

from dataclasses import dataclass

from pricetime.orders import Side

BASIS_POINTS = 10_000


@dataclass(frozen=True, slots=True, kw_only=True)
class PriceBand:
    """The day's permitted price range, in ticks, inclusive at both ends.

    Exchanges publish one per instrument per day. NSE calls it the price band,
    and it acts as a circuit limit: an order priced outside it is rejected.
    """

    lower: int
    upper: int

    def __post_init__(self) -> None:
        if not 0 < self.lower <= self.upper:
            raise ValueError(f"price band must satisfy 0 < lower <= upper, got {self}")

    def contains(self, price: int) -> bool:
        """True if an order may be priced at `price`."""
        return self.lower <= price <= self.upper


@dataclass(frozen=True, slots=True, kw_only=True)
class MarketRules:
    """Per-instrument, per-session parameters, fixed for the life of one engine.

    The journal records them in its header, so a replay enforces exactly the
    rules the original run did.

    Attributes:
        price_band: The day's price band, or None if the instrument has none.
        protection_bps: How far from the last traded price a market order may
            trade, in basis points (hundredths of a percent). NSE's market price
            protection calls this X%.
        protection_min_ticks: The narrowest the protection band may be, however
            low the price: NSE's "minimum absolute value".
        opening_price: The last traded price the session starts from, such as a
            pre-open auction's equilibrium price. With None, market orders are
            rejected until the first trade of the session, as on NSE.
    """

    price_band: PriceBand | None = None
    protection_bps: int
    protection_min_ticks: int
    opening_price: int | None = None

    def __post_init__(self) -> None:
        if self.protection_bps < 0 or self.protection_min_ticks < 0:
            raise ValueError(f"protection settings must not be negative, got {self}")
        if self.opening_price is not None and (
            self.opening_price <= 0
            or (self.price_band is not None and not self.price_band.contains(self.opening_price))
        ):
            raise ValueError(f"opening price must be positive and inside the band, got {self}")

    def protection_limit(self, side: Side, last_trade_price: int) -> int:
        """The furthest price a market order may trade at, given the last traded price.

        The band is X% of the last traded price, rounded down so it never exceeds
        X%, but never narrower than the minimum width.
        """
        width = max(
            last_trade_price * self.protection_bps // BASIS_POINTS, self.protection_min_ticks
        )
        return last_trade_price + width if side is Side.BUY else last_trade_price - width
