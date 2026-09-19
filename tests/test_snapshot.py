from pricetime.commands import ModifyOrder
from tests.support import buy, new_engine, random_commands


def test_equal_states_have_equal_digests() -> None:
    first, second = new_engine(), new_engine()
    for command in random_commands(seed=3, count=200):
        first.process(command)
        second.process(command)

    assert first.snapshot().digest() == second.snapshot().digest()


def test_digest_changes_when_an_order_changes() -> None:
    engine = new_engine()
    engine.process(buy(100, 10))
    before = engine.snapshot().digest()

    engine.process(ModifyOrder(order_id=1, price=100, quantity=9))

    assert engine.snapshot().digest() != before
