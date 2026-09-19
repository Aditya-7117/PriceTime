"""The trading rules an engine enforces for its instrument during one session."""

from dataclasses import dataclass


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
    """

    price_band: PriceBand | None = None
