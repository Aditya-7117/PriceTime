from pathlib import Path

import pytest

from pricetime.demo import SYMBOL, TICK_SIZE, main, record, script
from pricetime.journal import read_journal, replay
from pricetime.viewer.render import Session, build

SESSION = Session(symbol=SYMBOL, tick_size=TICK_SIZE)


def events_of(journal: Path) -> list[str]:
    steps = build(journal, SESSION)["steps"]
    assert isinstance(steps, list)
    return [event["text"] for step in steps for event in step["events"]]


@pytest.mark.parametrize(
    "rule",
    [
        "kept its place",
        "went to the back",
        "unfilled ioc",
        "rested as a limit order",
        "price protection",
        "price out of band",
        "self trade",
        "requested",
    ],
)
def test_the_demo_shows_every_rule_the_engine_enforces(tmp_path: Path, rule: str) -> None:
    assert any(rule in event for event in events_of(record(tmp_path / "session.jsonl")))


def test_the_demo_prevents_a_self_trade_in_both_directions(tmp_path: Path) -> None:
    prevented = [
        event for event in events_of(record(tmp_path / "s.jsonl")) if "self trade" in event
    ]

    # The first cancels the order that arrived; the second cancels the one already resting.
    assert prevented == [
        "order 16 cancelled, 40 left: self trade",
        "order 15 cancelled, 40 left: self trade",
    ]


def test_the_demo_records_who_each_order_belongs_to(tmp_path: Path) -> None:
    records = list(read_journal(record(tmp_path / "session.jsonl")))

    assert len(records) == len(script())
    assert records[0].annotation == {
        "session": "DEMO",
        "clordid": "DEMO-1",
        "account": "DEMO-ACC",
    }


def test_the_demo_is_the_same_session_every_time(tmp_path: Path) -> None:
    once = record(tmp_path / "once.jsonl").read_bytes()
    twice = record(tmp_path / "twice.jsonl").read_bytes()

    assert once == twice


def test_recording_again_replaces_the_old_journal(tmp_path: Path) -> None:
    journal = tmp_path / "session.jsonl"
    record(journal)

    assert replay(record(journal)).snapshot() == replay(journal).snapshot()
    assert len(list(read_journal(journal))) == len(script())


def test_the_demo_writes_a_journal_and_a_page(tmp_path: Path) -> None:
    journal = tmp_path / "demo" / "session.jsonl"
    page = tmp_path / "index.html"

    assert main(["--journal", str(journal), "--output", str(page)]) == 0
    assert journal.exists()
    assert SYMBOL in page.read_text(encoding="utf-8")
