import json
import re
from decimal import Decimal
from pathlib import Path
from typing import get_args

import pytest

from pricetime.commands import CancelOrder, Command, ModifyOrder
from pricetime.engine import MatchingEngine
from pricetime.events import Event
from pricetime.journal import JournaledEngine, read_journal
from pricetime.orders import Side
from pricetime.rules import MarketRules, PriceBand
from pricetime.viewer.render import (
    COMMAND_KINDS,
    DATA_TOKEN,
    EVENT_KINDS,
    TEMPLATE,
    Session,
    build,
    main,
    render,
    write,
)
from tests.support import buy, sell

RULES = MarketRules(
    price_band=PriceBand(lower=90, upper=110),
    protection_bps=500,
    protection_min_ticks=2,
    opening_price=100,
)
SESSION = Session(symbol="TESTCO", tick_size=Decimal("0.05"))
SCRIPT: list[Command] = [
    buy(99, 50),
    buy(98, 40),
    sell(101, 30),
    sell(100, 60),
    buy(101, 70),
    ModifyOrder(order_id=2, price=99, quantity=40),
    CancelOrder(order_id=1),
    buy(200, 10),
    CancelOrder(order_id=999),
    ModifyOrder(order_id=999, price=99, quantity=10),
]


def journal_of(tmp_path: Path, commands: list[Command], rules: MarketRules = RULES) -> Path:
    path = tmp_path / "session.jsonl"
    with JournaledEngine(path, rules) as engine:
        for command in commands:
            engine.process(command)
    return path


def payload_of(page: str) -> dict[str, object]:
    quoted = re.search(r'JSON\.parse\((".*?")\);', page, re.S)
    assert quoted is not None
    payload: dict[str, object] = json.loads(json.loads(quoted.group(1)))
    return payload


def test_every_step_is_the_book_as_the_engine_left_it(tmp_path: Path) -> None:
    journal = journal_of(tmp_path, SCRIPT)
    steps = build(journal, SESSION)["steps"]
    assert isinstance(steps, list)

    engine = MatchingEngine(RULES)
    for step, record in zip(steps, read_journal(journal), strict=True):
        engine.process(record.command)
        assert step["sequence"] == record.sequence
        assert step["bids"] == [
            [level.price, level.total_quantity, len(level)] for level in engine.book.bids
        ]
        assert step["asks"] == [
            [level.price, level.total_quantity, len(level)] for level in engine.book.asks
        ]
        assert step["lastTradePrice"] == engine.snapshot().last_trade_price


def test_the_page_carries_the_rules_it_was_replayed_under(tmp_path: Path) -> None:
    data = build(journal_of(tmp_path, SCRIPT), SESSION)

    assert data["symbol"] == "TESTCO"
    assert data["tickSize"] == "0.05"
    assert data["priceBand"] == {"lower": 90, "upper": 110}
    assert data["protection"] == {"bps": 500, "minTicks": 2}


def test_reach_is_what_the_engine_would_allow(tmp_path: Path) -> None:
    steps = build(journal_of(tmp_path, SCRIPT), SESSION)["steps"]
    assert isinstance(steps, list)

    for step in steps:
        last = step["lastTradePrice"]
        assert step["reach"] == {
            "buy": RULES.protection_limit(Side.BUY, last),
            "sell": RULES.protection_limit(Side.SELL, last),
        }


def test_reach_is_unknown_until_something_trades(tmp_path: Path) -> None:
    quiet = MarketRules(protection_bps=500, protection_min_ticks=2)
    steps = build(journal_of(tmp_path, [buy(99, 10)], quiet), SESSION)["steps"]
    assert isinstance(steps, list)

    assert steps[0]["lastTradePrice"] is None
    assert steps[0]["reach"] is None


def test_the_page_says_what_was_asked_for_and_what_happened(tmp_path: Path) -> None:
    steps = build(journal_of(tmp_path, SCRIPT), SESSION)["steps"]
    assert isinstance(steps, list)

    sweep = steps[4]
    assert sweep["action"] == "limit"
    assert sweep["summary"] == "limit buy 70 at 5.05, day, client 1"
    assert [event["kind"] for event in sweep["events"]] == ["accepted", "trade", "trade"]
    # Trades stay in whole ticks; the page turns them into rupees when it draws them.
    assert sweep["trades"] == [
        {"price": 100, "quantity": 60, "aggressor": "buy"},
        {"price": 101, "quantity": 10, "aggressor": "buy"},
    ]


def test_the_page_explains_a_refusal(tmp_path: Path) -> None:
    steps = build(journal_of(tmp_path, SCRIPT), SESSION)["steps"]
    assert isinstance(steps, list)

    assert steps[-3]["events"][0]["text"] == "order 6 rejected: price out of band"
    assert steps[-2]["events"][0]["text"] == "cancel of order 999 refused: unknown order"
    assert steps[-1]["events"][0]["text"] == "replace of order 999 refused: unknown order"


def test_long_sessions_stop_at_the_limit(tmp_path: Path) -> None:
    short = Session(symbol="TESTCO", tick_size=Decimal("0.05"), max_steps=3)
    steps = build(journal_of(tmp_path, SCRIPT), short)["steps"]
    assert isinstance(steps, list)

    assert len(steps) == 3


def test_a_journal_without_a_header_cannot_be_replayed(tmp_path: Path) -> None:
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="no header"):
        build(empty, SESSION)


def test_every_event_and_command_has_a_kind_the_page_can_colour() -> None:
    assert set(get_args(Event.__value__)) == set(EVENT_KINDS)
    assert set(get_args(Command.__value__)) == set(COMMAND_KINDS)


def test_the_page_holds_the_payload_it_was_built_with(tmp_path: Path) -> None:
    data = build(journal_of(tmp_path, SCRIPT), SESSION)

    assert payload_of(render(data)) == data


def test_a_template_must_hold_exactly_one_slot_for_the_data(tmp_path: Path) -> None:
    for content in ("<p>nothing here</p>", f"{DATA_TOKEN} {DATA_TOKEN}"):
        template = tmp_path / "template.html"
        template.write_text(content, encoding="utf-8")
        with pytest.raises(ValueError, match="exactly once"):
            render({"steps": []}, template)


def test_the_page_asks_the_network_for_nothing() -> None:
    page = TEMPLATE.read_text(encoding="utf-8")

    assert "//" not in re.sub(r"https?://[^\s\"']*", "", page).replace("\\B", "")
    for outside in ("<link", "<script src", "@import", "fetch(", "XMLHttpRequest"):
        assert outside not in page


def test_the_page_has_every_element_its_script_reaches_for() -> None:
    page = TEMPLATE.read_text(encoding="utf-8")
    present = set(re.findall(r'\sid="([^"]+)"', page))

    assert set(re.findall(r'getElementById\("([^"]+)"\)', page)) <= present


def test_writing_a_page_creates_it_and_its_folder(tmp_path: Path) -> None:
    destination = tmp_path / "pages" / "index.html"

    written = write(journal_of(tmp_path, SCRIPT), destination, SESSION)

    assert written == destination
    assert payload_of(destination.read_text(encoding="utf-8"))["symbol"] == "TESTCO"


def test_the_command_line_builds_a_page(tmp_path: Path) -> None:
    journal = journal_of(tmp_path, SCRIPT)
    destination = tmp_path / "out.html"

    assert main([str(journal), "--symbol", "ACME", "-o", str(destination)]) == 0
    assert payload_of(destination.read_text(encoding="utf-8"))["symbol"] == "ACME"
