import json
import os
import subprocess
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import pytest
from hypothesis import given

from pricetime.commands import (
    CancelOrder,
    Command,
    ModifyOrder,
    NewLimitOrder,
    NewMarketOrder,
    Validity,
)
from pricetime.journal import (
    JournaledEngine,
    JournalError,
    JournalWriter,
    read_journal,
    read_rules,
    replay,
)
from pricetime.orders import Side
from pricetime.rules import MarketRules, PriceBand
from tests.strategies import command_sequences, market_rules
from tests.support import buy, new_engine, random_commands, sell

RULES = MarketRules(
    price_band=PriceBand(lower=90, upper=110),
    protection_bps=500,
    protection_min_ticks=2,
    opening_price=100,
)
HEADER = (
    '{"format":"pricetime-journal","version":1,"rules":{"price_band":{"lower":90,"upper":110},'
    '"protection_bps":500,"protection_min_ticks":2,"opening_price":100}}\n'
)
NO_BAND = (
    '"rules":{"price_band":null,"protection_bps":500,"protection_min_ticks":2,"opening_price":null}'
)

EVERY_KIND: list[Command] = [
    NewLimitOrder(side=Side.BUY, price=101, quantity=10),
    NewLimitOrder(side=Side.SELL, price=0, quantity=-1),
    NewLimitOrder(side=Side.SELL, price=102, quantity=3, validity=Validity.IOC),
    NewMarketOrder(side=Side.SELL, quantity=4),
    CancelOrder(order_id=1),
    ModifyOrder(order_id=1, price=102, quantity=7),
]


def write(path: Path, commands: list[Command], rules: MarketRules = RULES) -> None:
    with JournalWriter(path, rules) as writer:
        for command in commands:
            writer.append(command)


def test_commands_read_back_exactly_as_written(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"

    write(path, EVERY_KIND)

    assert list(read_journal(path)) == EVERY_KIND


def test_records_are_numbered_from_one_in_the_order_written(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"

    write(path, EVERY_KIND)

    header, *records = path.read_text().splitlines()
    assert json.loads(header)["format"] == "pricetime-journal"
    assert [json.loads(record)["seq"] for record in records] == [1, 2, 3, 4, 5, 6]


def test_reopening_a_journal_appends_rather_than_truncating(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    write(path, EVERY_KIND[:2])

    write(path, EVERY_KIND[2:])

    assert list(read_journal(path)) == EVERY_KIND
    records = path.read_text().splitlines()[1:]
    assert [json.loads(record)["seq"] for record in records] == [1, 2, 3, 4, 5, 6]


def test_an_empty_file_is_an_empty_journal(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.touch()

    assert list(read_journal(path)) == []
    write(path, EVERY_KIND[:1])
    assert list(read_journal(path)) == EVERY_KIND[:1]


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        pytest.param(
            '{"format":"other","version":1,' + NO_BAND + "}\n",
            "not a pricetime journal",
            id="format",
        ),
        pytest.param('{"format":"pricet', "line 1: incomplete header", id="torn header"),
        pytest.param(
            '{"format":"pricetime-journal","version":2,' + NO_BAND + "}\n",
            "unsupported version",
            id="version",
        ),
        pytest.param(
            '{"format":"pricetime-journal","version":1}\n', "line 1: market rules", id="no rules"
        ),
        pytest.param(
            '{"format":"pricetime-journal","version":1,"rules":{"price_band":{"lower":9},'
            '"protection_bps":500,"protection_min_ticks":2,"opening_price":null}}\n',
            "line 1: market rules",
            id="bad band",
        ),
        pytest.param(
            '{"format":"pricetime-journal","version":1,"rules":{"price_band":{"lower":20,"upper":10},'
            '"protection_bps":500,"protection_min_ticks":2,"opening_price":null}}\n',
            "line 1: market rules",
            id="inverted band",
        ),
        pytest.param(HEADER + "not json\n", "line 2: not valid JSON", id="json"),
        pytest.param(HEADER + "[1, 2]\n", "line 2: not a JSON object", id="object"),
        pytest.param(
            HEADER + '{"seq":1,"type":"stop","side":"buy","quantity":1}\n',
            "line 2: unknown command type",
            id="type",
        ),
        pytest.param(
            HEADER + '{"seq":1,"type":"cancel"}\n', "line 2: expected fields", id="missing field"
        ),
        pytest.param(
            HEADER + '{"seq":1,"type":"cancel","order_id":1,"price":5}\n',
            "line 2: expected fields",
            id="extra field",
        ),
        pytest.param(
            HEADER + '{"seq":1,"type":"limit","side":"buy","price":"101","quantity":1,'
            '"validity":"day"}\n',
            "line 2: price must be an integer",
            id="string price",
        ),
        pytest.param(
            HEADER + '{"seq":1,"type":"market","side":"buy","quantity":true,"validity":"day"}\n',
            "line 2: quantity must be an integer",
            id="boolean quantity",
        ),
        pytest.param(
            HEADER + '{"seq":1,"type":"limit","side":"buy","price":101,"quantity":1,'
            '"validity":"gtc"}\n',
            "line 2: validity must be",
            id="validity",
        ),
        pytest.param(
            HEADER + '{"seq":1,"type":"market","side":"up","quantity":1,"validity":"day"}\n',
            "line 2: side must be",
            id="side",
        ),
        pytest.param(
            HEADER + '{"seq":1,"type":"cancel","order_id":1}\n'
            '{"seq":3,"type":"cancel","order_id":2}\n',
            "line 3: expected sequence 2, found 3",
            id="gap",
        ),
        pytest.param(
            HEADER + '{"seq":1,"type":"cancel","order_id":1}',
            "line 2: incomplete final record",
            id="torn tail",
        ),
    ],
)
def test_reader_refuses_a_damaged_journal(tmp_path: Path, content: str, problem: str) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_text(content)

    with pytest.raises(JournalError, match=problem):
        list(read_journal(path))


def test_writer_refuses_to_append_to_a_damaged_journal(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.write_text(HEADER + "not json\n")

    with pytest.raises(JournalError, match="line 2"):
        JournalWriter(path, RULES)

    assert path.read_text() == HEADER + "not json\n"


def test_closing_twice_is_harmless(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    writer = JournalWriter(path, RULES)
    writer.append(EVERY_KIND[0])

    writer.close()
    writer.close()

    assert list(read_journal(path)) == EVERY_KIND[:1]


def test_journaled_engine_records_a_command_before_applying_it(tmp_path: Path) -> None:
    journaled = JournaledEngine(tmp_path / "journal.jsonl", RULES)
    journaled.process(buy(100, 10))
    journaled.close()

    with pytest.raises(JournalError, match="closed"):
        journaled.process(sell(100, 10))

    # The command could not be recorded, so it must not have been applied.
    assert journaled.engine.snapshot().sequence == 1
    assert journaled.engine.book.best_bid() == 100


def test_reopening_a_journaled_engine_recovers_its_state(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    commands = random_commands(seed=11, count=300)
    uninterrupted = new_engine(RULES)
    for command in commands:
        uninterrupted.process(command)

    with JournaledEngine(path, RULES) as first_run:
        for command in commands[:200]:
            first_run.process(command)
    with JournaledEngine(path, RULES) as second_run:
        for command in commands[200:]:
            second_run.process(command)

    assert second_run.engine.snapshot() == uninterrupted.snapshot()
    assert list(read_journal(path)) == commands


@given(market_rules, command_sequences())
def test_replaying_a_journal_reproduces_every_event_and_the_final_state(
    rules: MarketRules, commands: list[Command]
) -> None:
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "journal.jsonl"
        with JournaledEngine(path, rules) as live:
            live_events = [live.process(command) for command in commands]

        assert read_rules(path) == rules
        replayed = new_engine(rules)
        replayed_events = [replayed.process(command) for command in read_journal(path)]

        assert replayed_events == live_events
        assert replayed.snapshot() == live.engine.snapshot()
        assert replay(path).snapshot() == live.engine.snapshot()


def test_replay_in_fresh_interpreters_with_different_hash_seeds_reaches_the_same_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "journal.jsonl"
    commands = random_commands(seed=7, count=2_000)
    write(path, commands)
    expected = replay(path).snapshot().digest()
    script = (
        "import sys; from pathlib import Path; from pricetime.journal import replay; "
        "sys.stdout.write(replay(Path(sys.argv[1])).snapshot().digest())"
    )

    for hash_seed in ("0", "1", "4242"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed}
        # A fixed argument list and no shell: nothing here comes from outside the test.
        result = subprocess.run(  # noqa: S603
            [sys.executable, "-c", script, str(path)],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
        assert result.stdout == expected


def test_the_journal_header_records_the_market_rules(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"

    write(path, [])

    assert read_rules(path) == RULES
    assert path.read_text() == HEADER


def test_replay_applies_the_rules_the_journal_was_written_under(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    write(path, [buy(100, 5), buy(111, 5)])

    engine = replay(path)

    assert [o.order_id for o in engine.snapshot().bids] == [1]


def test_reopening_a_journal_under_different_rules_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    write(path, [buy(100, 5)])

    with pytest.raises(JournalError, match="different market rules"):
        JournaledEngine(path, replace(RULES, opening_price=101))

    assert list(read_journal(path)) == [buy(100, 5)]


def test_replaying_a_journal_with_no_header_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "journal.jsonl"
    path.touch()

    assert read_rules(path) is None
    with pytest.raises(JournalError, match="no header"):
        replay(path)
