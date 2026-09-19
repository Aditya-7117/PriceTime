import pytest

from pricetime.codec import (
    DecodeError,
    decode_command,
    decode_header,
    encode_command,
    encode_header,
)
from pricetime.commands import (
    CancelOrder,
    Command,
    ModifyOrder,
    NewLimitOrder,
    NewMarketOrder,
    Validity,
)
from pricetime.orders import SelfTradeAction, Side
from pricetime.rules import MarketRules, PriceBand


@pytest.mark.parametrize(
    "rules",
    [
        MarketRules(protection_bps=500, protection_min_ticks=2),
        MarketRules(
            price_band=PriceBand(lower=90, upper=110),
            protection_bps=250,
            protection_min_ticks=1,
            opening_price=100,
        ),
    ],
    ids=["no band, no opening price", "band and opening price"],
)
def test_a_header_decodes_to_the_rules_it_was_written_with(rules: MarketRules) -> None:
    assert decode_header(encode_header(rules)) == rules


@pytest.mark.parametrize(
    "command",
    [
        NewLimitOrder(side=Side.BUY, price=101, quantity=10, client_id=1),
        NewLimitOrder(
            side=Side.SELL,
            price=99,
            quantity=2,
            client_id=2,
            validity=Validity.IOC,
            self_trade=SelfTradeAction.CANCEL_PASSIVE,
        ),
        NewMarketOrder(side=Side.SELL, quantity=4, client_id=3, validity=Validity.IOC),
        CancelOrder(order_id=3),
        ModifyOrder(order_id=3, price=99, quantity=7),
    ],
    ids=["limit", "ioc limit", "market", "cancel", "modify"],
)
def test_a_command_decodes_to_itself_and_its_sequence_number(command: Command) -> None:
    assert decode_command(encode_command(42, command)) == (42, command, None)


def test_an_annotation_travels_with_the_command() -> None:
    cancel = CancelOrder(order_id=3)
    reference = {"session": "CLIENT1", "clordid": "A-1"}

    line = encode_command(7, cancel, reference)

    assert decode_command(line) == (7, cancel, reference)


def test_a_malformed_annotation_is_refused() -> None:
    line = encode_command(7, CancelOrder(order_id=3)).removesuffix("}") + ',"ref":{"session":5}}'

    with pytest.raises(DecodeError, match="ref must map names to text"):
        decode_command(line)


@pytest.mark.parametrize(
    ("line", "problem"),
    [
        ("not json", "not a pricetime journal"),
        (
            '{"format":"pricetime-journal","version":1,"rules":{"band":null}}',
            "market rules must hold exactly",
        ),
        (
            '{"format":"pricetime-journal","version":1,"rules":[]}',
            "market rules must hold exactly",
        ),
        (
            '{"format":"pricetime-journal","version":1,"rules":{"price_band":'
            '{"lower":"90","upper":110},"protection_bps":500,"protection_min_ticks":2,'
            '"opening_price":null}}',
            "market rules: lower must be an integer",
        ),
        (
            '{"format":"pricetime-journal","version":1,"rules":{"price_band":null,'
            '"protection_bps":500,"protection_min_ticks":2,"opening_price":"100"}}',
            "market rules: opening_price must be an integer",
        ),
        (
            '{"format":"pricetime-journal","version":1,"rules":{"price_band":null,'
            '"protection_bps":-5,"protection_min_ticks":2,"opening_price":null}}',
            "market rules: protection",
        ),
    ],
)
def test_a_malformed_header_is_refused(line: str, problem: str) -> None:
    with pytest.raises(DecodeError, match=problem):
        decode_header(line)
