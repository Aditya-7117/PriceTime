from pricetime.commands import ModifyOrder
from pricetime.engine import MatchingEngine
from tests.support import buy, random_commands


def test_equal_states_have_equal_digests() -> None:
    first, second = MatchingEngine(), MatchingEngine()
    for command in random_commands(seed=3, count=200):
        first.process(command)
        second.process(command)

    assert first.snapshot().digest() == second.snapshot().digest()


def test_digest_changes_when_an_order_changes() -> None:
    engine = MatchingEngine()
    engine.process(buy(100, 10))
    before = engine.snapshot().digest()

    engine.process(ModifyOrder(order_id=1, price=100, quantity=9))

    assert engine.snapshot().digest() != before
