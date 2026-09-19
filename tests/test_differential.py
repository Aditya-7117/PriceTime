"""The engine against a deliberately naive reference book: same events, same state, always."""

import pytest
from hypothesis import given

from pricetime.commands import Command
from pricetime.rules import MarketRules
from tests.reference import ReferenceBook
from tests.strategies import command_sequences, market_rules
from tests.support import DEFAULT_RULES, new_engine, random_commands


@given(market_rules, command_sequences())
def test_the_engine_matches_the_reference_book_event_for_event(
    rules: MarketRules, commands: list[Command]
) -> None:
    engine, reference = new_engine(rules), ReferenceBook(rules)
    for command in commands:
        assert engine.process(command) == reference.process(command)
        assert engine.snapshot() == reference.snapshot()


@pytest.mark.parametrize("seed", range(5))
def test_the_engine_matches_the_reference_book_over_long_sessions(seed: int) -> None:
    engine, reference = new_engine(DEFAULT_RULES), ReferenceBook(DEFAULT_RULES)
    for command in random_commands(seed=seed, count=3_000):
        assert engine.process(command) == reference.process(command)
    assert engine.snapshot() == reference.snapshot()
