"""An exchange refuses to start on a configuration that cannot be right."""

from decimal import Decimal
from pathlib import Path

import pytest

from pricetime.fix.config import ExchangeConfig, Instrument, SessionSettings
from pricetime.rules import MarketRules

RULES = MarketRules(protection_bps=500, protection_min_ticks=2)


def instrument(symbol: str = "DEMOCO", tick_size: Decimal = Decimal("0.05")) -> Instrument:
    return Instrument(symbol=symbol, journal=Path("demo.jsonl"), rules=RULES, tick_size=tick_size)


def test_an_instrument_needs_a_symbol() -> None:
    with pytest.raises(ValueError, match="needs a symbol"):
        instrument(symbol="")


@pytest.mark.parametrize("tick_size", [Decimal(0), Decimal("-0.05")])
def test_a_tick_size_must_be_positive(tick_size: Decimal) -> None:
    with pytest.raises(ValueError, match="tick size must be positive"):
        instrument(tick_size=tick_size)


def test_an_order_rate_limit_cannot_be_negative() -> None:
    with pytest.raises(ValueError, match="cannot be negative"):
        SessionSettings(comp_id="CLIENT1", store=Path("s.jsonl"), orders_per_second=-1)


def test_a_session_with_no_limit_is_not_throttled() -> None:
    assert not SessionSettings(comp_id="CLIENT1", store=Path("s.jsonl")).throttled
    assert SessionSettings(comp_id="CLIENT1", store=Path("s.jsonl"), orders_per_second=5).throttled


def test_the_exchange_needs_its_own_company_id() -> None:
    with pytest.raises(ValueError, match="CompID of its own"):
        ExchangeConfig(comp_id="", instruments={"DEMOCO": instrument()})


def test_an_instrument_filed_under_the_wrong_symbol_is_refused() -> None:
    with pytest.raises(ValueError, match="filed under"):
        ExchangeConfig(comp_id="PRICETIME", instruments={"OTHER": instrument()})


def test_a_session_filed_under_the_wrong_company_is_refused() -> None:
    with pytest.raises(ValueError, match="filed under"):
        ExchangeConfig(
            comp_id="PRICETIME",
            instruments={"DEMOCO": instrument()},
            sessions={"OTHER": SessionSettings(comp_id="CLIENT1", store=Path("s.jsonl"))},
        )


def test_client_ids_start_at_one() -> None:
    with pytest.raises(ValueError, match="client IDs start at 1"):
        ExchangeConfig(
            comp_id="PRICETIME", instruments={"DEMOCO": instrument()}, accounts={"ACC-1": 0}
        )


def test_the_heartbeat_interval_must_be_positive() -> None:
    with pytest.raises(ValueError, match="heartbeat interval"):
        ExchangeConfig(
            comp_id="PRICETIME", instruments={"DEMOCO": instrument()}, heartbeat_interval=0
        )
