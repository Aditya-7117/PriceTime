"""Replay a journal into a single page that can be stepped through.

The page shows what the engine did and why: the book after every command, the
trades it caused, the price band, and how far a market order could have reached
before protection stopped it. It is read only. There is no order entry, because
this is a window onto an engine, not a trading screen.

Everything is written into one HTML file with the data inside it, so it opens
from a file, works offline, and can be published as it is.

Run it with `python -m pricetime.viewer <journal> --symbol DEMOCO`.
"""

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from pricetime.book import BookSide
from pricetime.commands import CancelOrder, Command, ModifyOrder, NewLimitOrder, NewMarketOrder
from pricetime.engine import MatchingEngine
from pricetime.events import (
    CancelRejected,
    Event,
    MarketOrderConverted,
    ModifyRejected,
    OrderAccepted,
    OrderCancelled,
    OrderModified,
    OrderRejected,
    Trade,
)
from pricetime.journal import read_journal, read_rules
from pricetime.orders import Side
from pricetime.prices import from_ticks
from pricetime.rules import MarketRules

TEMPLATE = Path(__file__).with_name("template.html")
DATA_TOKEN = '"__PRICETIME_DATA__"'  # noqa: S105 - the placeholder the page's data replaces
DEFAULT_STEPS = 1_500
RUPEE = "₹"

# The page colours and groups by these, rather than reading the sentences below.
EVENT_KINDS: Mapping[type[Event], str] = {
    OrderAccepted: "accepted",
    OrderRejected: "rejected",
    Trade: "trade",
    OrderCancelled: "cancelled",
    MarketOrderConverted: "converted",
    OrderModified: "modified",
    CancelRejected: "rejected",
    ModifyRejected: "rejected",
}
COMMAND_KINDS: Mapping[type[Command], str] = {
    NewLimitOrder: "limit",
    NewMarketOrder: "market",
    CancelOrder: "cancel",
    ModifyOrder: "replace",
}


@dataclass(frozen=True, slots=True)
class Session:
    """What the viewer needs to know about the session it is showing."""

    symbol: str
    tick_size: Decimal
    currency: str = RUPEE
    max_steps: int = DEFAULT_STEPS


def build(journal: Path, session: Session) -> dict[str, object]:
    """Replay a journal and collect what the page needs, step by step."""
    rules = read_rules(journal)
    if rules is None:
        raise ValueError(f"{journal} has no header, so there is nothing to replay")
    engine = MatchingEngine(rules)
    band = rules.price_band
    steps: list[dict[str, object]] = []

    for record in read_journal(journal):
        if len(steps) >= session.max_steps:
            break
        events = engine.process(record.command)
        last_trade_price = engine.snapshot().last_trade_price
        steps.append(
            {
                "sequence": record.sequence,
                "action": COMMAND_KINDS[type(record.command)],
                "summary": describe(record.command, session.tick_size),
                "events": [
                    {"kind": EVENT_KINDS[type(event)], "text": explain(event, session.tick_size)}
                    for event in events
                ],
                "bids": depth(engine.book.bids),
                "asks": depth(engine.book.asks),
                "lastTradePrice": last_trade_price,
                "reach": reach(rules, last_trade_price),
                "trades": [
                    {
                        "price": event.price,
                        "quantity": event.quantity,
                        "aggressor": event.aggressor.value,
                    }
                    for event in events
                    if isinstance(event, Trade)
                ],
            }
        )

    return {
        "symbol": session.symbol,
        "tickSize": str(session.tick_size),
        "currency": session.currency,
        "priceBand": None if band is None else {"lower": band.lower, "upper": band.upper},
        "protection": {
            "bps": rules.protection_bps,
            "minTicks": rules.protection_min_ticks,
        },
        "steps": steps,
    }


def reach(rules: MarketRules, last_trade_price: int | None) -> dict[str, int] | None:
    """How far a market order may trade on each side, as the engine would work it out.

    The page shows this rather than recomputing it, so what it draws is what the
    engine would actually allow.
    """
    if last_trade_price is None:
        return None
    return {
        "buy": rules.protection_limit(Side.BUY, last_trade_price),
        "sell": rules.protection_limit(Side.SELL, last_trade_price),
    }


def depth(side: BookSide) -> list[list[int]]:
    """Each price level on one side, best first: price in ticks, quantity, order count."""
    return [[level.price, level.total_quantity, len(level)] for level in side]


def describe(command: Command, tick_size: Decimal) -> str:
    """One line saying what was asked for."""
    match command:
        case NewLimitOrder():
            return (
                f"limit {command.side.value} {command.quantity} at "
                f"{money(command.price, tick_size)}, {command.validity.value}, client "
                f"{command.client_id}"
            )
        case NewMarketOrder():
            return (
                f"market {command.side.value} {command.quantity}, "
                f"{command.validity.value}, client {command.client_id}"
            )
        case CancelOrder():
            return f"cancel order {command.order_id}"
        case ModifyOrder():
            return (
                f"replace order {command.order_id} with {command.quantity} at "
                f"{money(command.price, tick_size)}"
            )


def explain(event: Event, tick_size: Decimal) -> str:
    """One line saying what happened."""
    said: str
    match event:
        case OrderAccepted():
            said = f"order {event.order_id} accepted"
        case OrderRejected():
            said = f"order {event.order_id} rejected: {words(event.reason.value)}"
        case Trade():
            said = (
                f"traded {event.quantity} at {money(event.price, tick_size)}: order "
                f"{event.taker_order_id} took order {event.maker_order_id}"
            )
        case OrderCancelled():
            said = (
                f"order {event.order_id} cancelled, {event.quantity} left: "
                f"{words(event.reason.value)}"
            )
        case MarketOrderConverted():
            said = (
                f"order {event.order_id} rested as a limit order at {money(event.price, tick_size)}"
            )
        case OrderModified():
            place = "kept its place" if event.kept_priority else "went to the back"
            said = (
                f"order {event.order_id} replaced: {event.remaining} at "
                f"{money(event.price, tick_size)}, {place}"
            )
        case CancelRejected():
            said = f"cancel of order {event.order_id} refused: {words(event.reason.value)}"
        case ModifyRejected():
            said = f"replace of order {event.order_id} refused: {words(event.reason.value)}"
    return said


def money(ticks: int, tick_size: Decimal) -> str:
    """A price in ticks, written the way a person reads it."""
    return str(from_ticks(ticks, tick_size))


def words(value: str) -> str:
    """An engine reason, in plain words."""
    return value.replace("_", " ")


def render(payload: dict[str, object], template: Path = TEMPLATE) -> str:
    """Put the data inside the page."""
    page = template.read_text(encoding="utf-8")
    if page.count(DATA_TOKEN) != 1:
        raise ValueError(f"{template} must hold {DATA_TOKEN} exactly once")
    return page.replace(DATA_TOKEN, json.dumps(json.dumps(payload, separators=(",", ":"))))


def write(journal: Path, destination: Path, session: Session) -> Path:
    """Build the page for a journal and write it out."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render(build(journal, session)), encoding="utf-8")
    return destination


def main(argv: Sequence[str] | None = None) -> int:
    """Build a viewer page from a journal."""
    parser = argparse.ArgumentParser(description="Replay a journal into a page.")
    parser.add_argument("journal", type=Path, help="the journal to replay")
    parser.add_argument("--symbol", default="DEMOCO", help="the instrument's symbol")
    parser.add_argument("--tick-size", type=Decimal, default=Decimal("0.05"))
    parser.add_argument("--currency", default=RUPEE)
    parser.add_argument("--max-steps", type=int, default=DEFAULT_STEPS)
    parser.add_argument("--output", "-o", type=Path, default=Path("viewer.html"))
    arguments = parser.parse_args(argv)

    session = Session(
        symbol=arguments.symbol,
        tick_size=arguments.tick_size,
        currency=arguments.currency,
        max_steps=arguments.max_steps,
    )
    written = write(arguments.journal, arguments.output, session)
    sys.stdout.write(f"{written}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
