"""What an exchange is set up with: its instruments, its accounts and its sessions.

An account must be registered before it can trade, which is how NSE works: a
client code is known to the exchange before an order arrives, not invented by
whoever sends one.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from pricetime.rules import MarketRules

DEFAULT_TICK_SIZE = Decimal("0.05")


@dataclass(frozen=True, slots=True)
class Instrument:
    """One tradeable instrument: its tick size, its rules for the day, and its journal."""

    symbol: str
    journal: Path
    rules: MarketRules
    tick_size: Decimal = DEFAULT_TICK_SIZE

    def __post_init__(self) -> None:
        if not self.symbol:
            raise ValueError("an instrument needs a symbol")
        if self.tick_size <= 0:
            raise ValueError(f"{self.symbol}: tick size must be positive, got {self.tick_size}")


@dataclass(frozen=True, slots=True)
class SessionSettings:
    """One counterparty's connection: its limits and what happens when it drops."""

    comp_id: str
    store: Path
    orders_per_second: int = 0
    burst: int = 0
    cancel_on_disconnect: bool = False

    def __post_init__(self) -> None:
        if self.orders_per_second < 0 or self.burst < 0:
            raise ValueError(f"{self.comp_id}: order rate limits cannot be negative")

    @property
    def throttled(self) -> bool:
        """True if this session has an order rate limit at all."""
        return self.orders_per_second > 0


@dataclass(frozen=True, slots=True)
class ExchangeConfig:
    """Everything the gateway needs to know before the first connection arrives."""

    comp_id: str
    instruments: Mapping[str, Instrument]
    accounts: Mapping[str, int] = field(default_factory=dict)
    sessions: Mapping[str, SessionSettings] = field(default_factory=dict)
    heartbeat_interval: int = 30

    def __post_init__(self) -> None:
        if not self.comp_id:
            raise ValueError("the exchange needs a CompID of its own")
        for symbol, instrument in self.instruments.items():
            if symbol != instrument.symbol:
                raise ValueError(f"instrument {instrument.symbol} is filed under {symbol}")
        for comp_id, settings in self.sessions.items():
            if comp_id != settings.comp_id:
                raise ValueError(f"session {settings.comp_id} is filed under {comp_id}")
        if any(client_id < 1 for client_id in self.accounts.values()):
            raise ValueError("client IDs start at 1")
        if self.heartbeat_interval < 1:
            raise ValueError("the heartbeat interval is in seconds and must be positive")
