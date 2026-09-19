import pytest

from pricetime.codec import (
    DecodeError,
    decode_command,
    decode_header,
    encode_command,
    encode_header,
)
from pricetime.commands import CancelOrder, Command, ModifyOrder, NewLimitOrder, NewMarketOrder
from pricetime.orders import Side
from pricetime.rules import MarketRules, PriceBand


@pytest.mark.parametrize(
    "rules",
    [MarketRules(), MarketRules(price_band=PriceBand(lower=90, upper=110))],
    ids=["no band", "band"],
)
def test_a_header_decodes_to_the_rules_it_was_written_with(rules: MarketRules) -> None:
    assert decode_header(encode_header(rules)) == rules


@pytest.mark.parametrize(
    "command",
    [
        NewLimitOrder(side=Side.BUY, price=101, quantity=10),
        NewMarketOrder(side=Side.SELL, quantity=4),
        CancelOrder(order_id=3),
        ModifyOrder(order_id=3, price=99, quantity=7),
    ],
    ids=["limit", "market", "cancel", "modify"],
)
def test_a_command_decodes_to_itself_and_its_sequence_number(command: Command) -> None:
    assert decode_command(encode_command(42, command)) == (42, command)


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ("not json", "not a pricetime journal"),
        (
            '{"format":"pricetime-journal","version":1,"rules":{"band":null}}',
            "market rules must hold exactly price_band",
        ),
        (
            '{"format":"pricetime-journal","version":1,"rules":[]}',
            "market rules must hold exactly price_band",
        ),
        (
            '{"format":"pricetime-journal","version":1,'
            '"rules":{"price_band":{"lower":"90","upper":110}}}',
            "market rules: lower must be an integer",
        ),
    ],
)
def test_a_malformed_header_is_refused(line: str, problem: str) -> None:
    with pytest.raises(DecodeError, match=problem):
        decode_header(line)
