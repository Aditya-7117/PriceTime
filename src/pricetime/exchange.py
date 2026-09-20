"""Run a small exchange over FIX, and trade against it, from the command line.

Two commands, so the whole thing can be seen working without writing any code:

    python -m pricetime.exchange serve
    python -m pricetime.exchange trade

`serve` listens for FIX connections and runs one instrument. `trade` logs on as
a client, rests an offer, crosses it from a second account, cancels what is
left, and prints every execution report the exchange sends back.

Everything it writes, the journal and the session files, goes under one folder,
so a session survives a restart of either side and deleting the folder starts a
fresh day.
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from pricetime.demo import DEMO_RULES, SYMBOL, TICK_SIZE
from pricetime.fix import tags
from pricetime.fix.client import ClientSettings, ExchangeClient
from pricetime.fix.config import ExchangeConfig, Instrument, SessionSettings
from pricetime.fix.server import ExchangeServer
from pricetime.fix.tags import ExecType, FixSide, MsgType, OrdType, TimeInForce
from pricetime.fix.wire import Fields

EXCHANGE = "PRICETIME"
TRADER = "CLIENT1"
SELLER = "ACC-1"
BUYER = "ACC-2"
DEFAULT_PORT = 5001
DEFAULT_FOLDER = Path("run")
QUIET = 0.5

WORDS = {
    ExecType.NEW: "resting",
    ExecType.TRADE: "traded",
    ExecType.CANCELED: "cancelled",
    ExecType.REPLACED: "replaced",
    ExecType.REJECTED: "rejected",
    ExecType.RESTATED: "restated",
}


def exchange_config(folder: Path) -> ExchangeConfig:
    """One instrument, two registered accounts and one session, all kept under `folder`."""
    folder.mkdir(parents=True, exist_ok=True)
    return ExchangeConfig(
        comp_id=EXCHANGE,
        instruments={
            SYMBOL: Instrument(
                symbol=SYMBOL,
                journal=folder / f"{SYMBOL}.jsonl",
                rules=DEMO_RULES,
                tick_size=TICK_SIZE,
            )
        },
        accounts={SELLER: 1, BUYER: 2},
        sessions={TRADER: SessionSettings(comp_id=TRADER, store=folder / f"{TRADER}.jsonl")},
    )


def stamp() -> str:
    """Now, in the format FIX writes a timestamp."""
    return datetime.now(UTC).strftime("%Y%m%d-%H:%M:%S.%f")[:-3]


def new_order(order_id: str, account: str, side: FixSide, price: str, quantity: int) -> Fields:
    """A NewOrderSingle (35=D) for a day limit order."""
    return (
        (tags.MSG_TYPE, MsgType.NEW_ORDER_SINGLE),
        (tags.CL_ORD_ID, order_id),
        (tags.ACCOUNT, account),
        (tags.SYMBOL, SYMBOL),
        (tags.SIDE, side),
        (tags.ORDER_QTY, str(quantity)),
        (tags.ORD_TYPE, OrdType.LIMIT),
        (tags.PRICE, price),
        (tags.TIME_IN_FORCE, TimeInForce.DAY),
        (tags.TRANSACT_TIME, stamp()),
    )


def cancel_order(order_id: str, original: str, side: FixSide) -> Fields:
    """An OrderCancelRequest (35=F) for an order sent earlier."""
    return (
        (tags.MSG_TYPE, MsgType.ORDER_CANCEL_REQUEST),
        (tags.CL_ORD_ID, order_id),
        (tags.ORIG_CL_ORD_ID, original),
        (tags.SYMBOL, SYMBOL),
        (tags.SIDE, side),
        (tags.TRANSACT_TIME, stamp()),
    )


def describe(report: Fields) -> str:
    """One readable line for whatever the exchange sent back."""
    message = dict(report)
    if message.get(tags.MSG_TYPE) != MsgType.EXECUTION_REPORT:
        return (
            f"{message.get(tags.CL_ORD_ID, '?')}: refused"
            f" ({message.get(tags.TEXT, 'no reason given')})"
        )
    said = WORDS.get(ExecType(message[tags.EXEC_TYPE]), message[tags.EXEC_TYPE])
    line = f"{message[tags.CL_ORD_ID]}: {said}"
    if message[tags.EXEC_TYPE] == ExecType.TRADE:
        line += f" {message[tags.LAST_QTY]} at {message[tags.LAST_PX]}"
    if message[tags.EXEC_TYPE] == ExecType.REJECTED:
        line += f" ({message.get(tags.TEXT, 'no reason given')})"
    return (
        f"{line}, {message[tags.LEAVES_QTY]} left of {message[tags.CUM_QTY]} done"
        f", exchange order {message[tags.ORDER_ID]}"
    )


async def drain(client: ExchangeClient, into: list[Fields]) -> None:
    """Collect reports until the exchange goes quiet."""
    while True:
        try:
            into.append(await client.report(timeout=QUIET))
        except TimeoutError:
            return


async def trade(host: str, port: int, folder: Path) -> list[Fields]:
    """Log on, rest an offer, cross it from another account, cancel the rest."""
    folder.mkdir(parents=True, exist_ok=True)
    client = ExchangeClient(
        host,
        port,
        ClientSettings(comp_id=TRADER, target_comp_id=EXCHANGE, store=folder / "trader.jsonl"),
    )
    reports: list[Fields] = []
    await client.logon()
    try:
        await client.send(new_order("SELL-1", SELLER, FixSide.SELL, "100.00", 100))
        await drain(client, reports)
        await client.send(new_order("BUY-1", BUYER, FixSide.BUY, "100.00", 60))
        await drain(client, reports)
        await client.send(cancel_order("SELL-2", "SELL-1", FixSide.SELL))
        await drain(client, reports)
    finally:
        await client.close()
    return reports


async def serve(host: str, port: int, folder: Path) -> None:
    """Listen until interrupted."""
    server = ExchangeServer(exchange_config(folder), host=host, port=port)
    await server.start()
    sys.stdout.write(
        f"{EXCHANGE} is listening on {host}:{server.port}\n"
        f"instrument {SYMBOL}, tick {TICK_SIZE}, accounts {SELLER} and {BUYER}\n"
        f"journal and session files under {folder}/\n"
        f"trade against it with: python -m pricetime.exchange trade --port {server.port}\n"
    )
    sys.stdout.flush()
    try:
        await asyncio.Event().wait()
    finally:
        await server.close()


def main(argv: Sequence[str] | None = None) -> int:
    """Serve an exchange, or trade against one that is already serving."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=("serve", "trade"))
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--folder", type=Path, default=DEFAULT_FOLDER)
    arguments = parser.parse_args(argv)

    if arguments.command == "serve":
        logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
        try:
            asyncio.run(serve(arguments.host, arguments.port, arguments.folder))
        except KeyboardInterrupt:
            sys.stdout.write("\nclosed\n")
        return 0

    for report in asyncio.run(trade(arguments.host, arguments.port, arguments.folder)):
        sys.stdout.write(f"{describe(report)}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
