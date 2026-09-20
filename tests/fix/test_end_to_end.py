"""The whole thing over a real socket: FIX in, matching, execution reports back."""

import asyncio
from collections.abc import Coroutine
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from pricetime.fix import tags
from pricetime.fix.client import ClientSettings, ExchangeClient
from pricetime.fix.config import ExchangeConfig, Instrument, SessionSettings
from pricetime.fix.server import ExchangeServer
from pricetime.fix.tags import ExecType, FixSide, MsgType, OrdType, TimeInForce
from pricetime.fix.wire import Fields
from pricetime.rules import MarketRules, PriceBand

EXCHANGE = "PRICETIME"
FIRST = "CLIENT1"
SECOND = "CLIENT2"
SYMBOL = "DEMOCO"
TICK = Decimal("0.05")
RULES = MarketRules(
    price_band=PriceBand(lower=1_800, upper=2_200),
    protection_bps=500,
    protection_min_ticks=2,
    opening_price=2_000,
)


def run[T](scenario: Coroutine[Any, Any, T]) -> T:
    """Run one scenario, with a hard limit so a broken test cannot hang the suite."""
    return asyncio.run(_with_timeout(scenario))


async def _with_timeout[T](scenario: Coroutine[Any, Any, T]) -> T:
    return await asyncio.wait_for(scenario, timeout=20)


def config(tmp_path: Path, *, cancel_on_disconnect: bool = False) -> ExchangeConfig:
    return ExchangeConfig(
        comp_id=EXCHANGE,
        instruments={
            SYMBOL: Instrument(
                symbol=SYMBOL, journal=tmp_path / f"{SYMBOL}.jsonl", rules=RULES, tick_size=TICK
            )
        },
        accounts={"ACC-1": 1, "ACC-2": 2},
        sessions={
            FIRST: SessionSettings(
                comp_id=FIRST,
                store=tmp_path / "first-session.jsonl",
                cancel_on_disconnect=cancel_on_disconnect,
            ),
            SECOND: SessionSettings(comp_id=SECOND, store=tmp_path / "second-session.jsonl"),
        },
        heartbeat_interval=5,
    )


def order(
    client_order_id: str,
    side: FixSide = FixSide.BUY,
    price: str = "100.00",
    quantity: int = 10,
    account: str = "ACC-1",
) -> Fields:
    return (
        (tags.MSG_TYPE, MsgType.NEW_ORDER_SINGLE),
        (tags.CL_ORD_ID, client_order_id),
        (tags.ACCOUNT, account),
        (tags.SYMBOL, SYMBOL),
        (tags.SIDE, side),
        (tags.ORDER_QTY, str(quantity)),
        (tags.ORD_TYPE, OrdType.LIMIT),
        (tags.PRICE, price),
        (tags.TIME_IN_FORCE, TimeInForce.DAY),
        (tags.TRANSACT_TIME, "20260921-09:15:00.000"),
    )


def settings(comp_id: str, store: Path | None = None) -> ClientSettings:
    return ClientSettings(
        comp_id=comp_id, target_comp_id=EXCHANGE, heartbeat_interval=5, store=store
    )


def test_an_order_sent_over_a_socket_comes_back_acknowledged(tmp_path: Path) -> None:
    async def scenario() -> dict[int, str]:
        server = ExchangeServer(config(tmp_path))
        await server.start()
        try:
            async with ExchangeClient("127.0.0.1", server.port, settings(FIRST)) as client:
                await client.send(order("A-1"))
                return dict(await client.report())
        finally:
            await server.close()

    report = run(scenario())

    assert report[tags.EXEC_TYPE] == ExecType.NEW
    assert report[tags.CL_ORD_ID] == "A-1"
    assert report[tags.ORDER_ID] == f"{SYMBOL}:1"
    assert report[tags.LEAVES_QTY] == "10"


def test_two_clients_trade_and_both_are_told(tmp_path: Path) -> None:
    async def scenario() -> tuple[dict[int, str], dict[int, str]]:
        server = ExchangeServer(config(tmp_path))
        await server.start()
        try:
            async with (
                ExchangeClient("127.0.0.1", server.port, settings(FIRST)) as buyer,
                ExchangeClient("127.0.0.1", server.port, settings(SECOND)) as seller,
            ):
                await buyer.send(order("A-1", FixSide.BUY, "100.00", 10))
                await buyer.report()  # the acknowledgement
                await seller.send(order("B-1", FixSide.SELL, "100.00", 4, account="ACC-2"))
                await seller.report()  # the acknowledgement
                return dict(await buyer.report()), dict(await seller.report())
        finally:
            await server.close()

    maker, taker = run(scenario())

    assert maker[tags.EXEC_TYPE] == ExecType.TRADE
    assert maker[tags.CL_ORD_ID] == "A-1"
    assert maker[tags.LAST_QTY] == "4"
    assert maker[tags.LAST_PX] == "100.00"
    assert taker[tags.EXEC_TYPE] == ExecType.TRADE
    assert taker[tags.CL_ORD_ID] == "B-1"
    assert taker[tags.LEAVES_QTY] == "0"


def test_a_client_that_vanishes_has_its_orders_cancelled_and_hears_about_it_later(
    tmp_path: Path,
) -> None:
    """The whole persistence story: crash, cancel on disconnect, reconnect, resend."""

    async def scenario() -> dict[int, str]:
        server = ExchangeServer(config(tmp_path, cancel_on_disconnect=True))
        await server.start()
        store = tmp_path / "client-session.jsonl"
        try:
            client = ExchangeClient("127.0.0.1", server.port, settings(FIRST, store))
            await client.logon()
            await client.send(order("A-1"))
            await client.report()  # the acknowledgement
            await client.drop()  # the client crashes without logging out
            await asyncio.sleep(0.2)  # the exchange notices

            back = ExchangeClient("127.0.0.1", server.port, settings(FIRST, store))
            await back.logon()
            missed = dict(await back.report())
            await back.close()
            return missed
        finally:
            await server.close()

    missed = run(scenario())

    assert missed[tags.EXEC_TYPE] == ExecType.CANCELED
    assert missed[tags.CL_ORD_ID] == "A-1"
    assert missed[tags.LEAVES_QTY] == "0"
    assert missed[tags.POSS_DUP_FLAG] == "Y"  # it was first sent while the line was down


def test_a_client_that_logs_out_keeps_its_orders(tmp_path: Path) -> None:
    async def scenario() -> int:
        server = ExchangeServer(config(tmp_path, cancel_on_disconnect=True))
        await server.start()
        store = tmp_path / "client-session.jsonl"
        try:
            async with ExchangeClient("127.0.0.1", server.port, settings(FIRST, store)) as client:
                await client.send(order("A-1"))
                await client.report()
            await asyncio.sleep(0.2)

            back = ExchangeClient("127.0.0.1", server.port, settings(FIRST, store))
            await back.logon()
            await asyncio.sleep(0.2)
            waiting = back.pending_reports()
            await back.close()
            return waiting
        finally:
            await server.close()

    assert run(scenario()) == 0  # nothing was cancelled, so there is nothing to report


def test_a_connection_from_a_stranger_is_dropped(tmp_path: Path) -> None:
    async def scenario() -> None:
        server = ExchangeServer(config(tmp_path))
        await server.start()
        try:
            client = ExchangeClient("127.0.0.1", server.port, settings("NOT-A-MEMBER"))
            with pytest.raises((TimeoutError, ConnectionError)):
                await client.logon(timeout=2)
            await client.drop()
        finally:
            await server.close()

    run(scenario())


def test_the_exchange_keeps_its_journal_across_a_restart(tmp_path: Path) -> None:
    async def first_run() -> None:
        server = ExchangeServer(config(tmp_path))
        await server.start()
        try:
            async with ExchangeClient("127.0.0.1", server.port, settings(SECOND)) as client:
                await client.send(order("B-1", FixSide.SELL, "101.00", 7, account="ACC-2"))
                await client.report()
        finally:
            await server.close()

    async def second_run() -> dict[int, str]:
        server = ExchangeServer(config(tmp_path))
        await server.start()
        try:
            async with ExchangeClient("127.0.0.1", server.port, settings(FIRST)) as client:
                await client.send(order("A-1", FixSide.BUY, "101.00", 7))
                await client.report()  # the acknowledgement
                return dict(await client.report())  # the trade against the older order
        finally:
            await server.close()

    run(first_run())
    trade = run(second_run())

    assert trade[tags.EXEC_TYPE] == ExecType.TRADE
    assert trade[tags.LAST_QTY] == "7"
    assert trade[tags.LAST_PX] == "101.00"


def test_the_time_of_a_report_is_when_it_was_written(tmp_path: Path) -> None:
    async def scenario() -> str:
        server = ExchangeServer(config(tmp_path))
        await server.start()
        try:
            async with ExchangeClient("127.0.0.1", server.port, settings(FIRST)) as client:
                await client.send(order("A-1"))
                return dict(await client.report())[tags.TRANSACT_TIME]
        finally:
            await server.close()

    stamped = run(scenario())

    written = datetime.strptime(stamped, "%Y%m%d-%H:%M:%S.%f").replace(tzinfo=UTC)
    assert abs((datetime.now(UTC) - written).total_seconds()) < 60
