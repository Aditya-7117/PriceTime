"""The command-line exchange: serve one, trade against it, read what it says."""

import asyncio
from pathlib import Path

from pricetime.demo import SYMBOL
from pricetime.exchange import describe, exchange_config, trade
from pricetime.fix import tags
from pricetime.fix.server import ExchangeServer
from pricetime.fix.tags import ExecType
from pricetime.fix.wire import Fields
from pricetime.journal import replay


async def session(folder: Path) -> list[Fields]:
    server = ExchangeServer(exchange_config(folder), port=0)
    await server.start()
    try:
        return await asyncio.wait_for(trade("127.0.0.1", server.port, folder), timeout=20)
    finally:
        await server.close()


def test_the_scripted_session_rests_trades_and_cancels(tmp_path: Path) -> None:
    reports = asyncio.run(session(tmp_path))

    assert [dict(report)[tags.EXEC_TYPE] for report in reports] == [
        ExecType.NEW,  # the offer rests
        ExecType.NEW,  # the buy is acknowledged before it trades
        ExecType.TRADE,  # the resting offer is told about its fill
        ExecType.TRADE,  # then the order that took it
        ExecType.CANCELED,  # and what was left of the offer is pulled
    ]


def test_every_report_reads_as_a_sentence(tmp_path: Path) -> None:
    lines = [describe(report) for report in asyncio.run(session(tmp_path))]

    assert lines[0] == "SELL-1: resting, 100 left of 0 done, exchange order DEMOCO:1"
    assert lines[2] == "SELL-1: traded 60 at 100.00, 40 left of 60 done, exchange order DEMOCO:1"
    assert lines[4] == "SELL-2: cancelled, 0 left of 60 done, exchange order DEMOCO:1"


def test_what_the_exchange_journaled_replays_to_the_same_book(tmp_path: Path) -> None:
    asyncio.run(session(tmp_path))

    book = replay(tmp_path / f"{SYMBOL}.jsonl").snapshot()

    assert book.bids == ()
    assert book.asks == ()
    assert book.last_trade_price == 2_000
