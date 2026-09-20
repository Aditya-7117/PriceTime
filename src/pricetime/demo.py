"""A short session that puts every rule of the engine on show.

Running `python -m pricetime.demo` writes two things: a journal, which is the
real thing the engine records, and a page built from that journal which can be
stepped through command by command.

The script is deliberate rather than random. It builds a book, sweeps it,
replaces orders both ways, cancels, and then walks through the rules that are
hard to picture from a README: an immediate-or-cancel order, a market order that
runs out of book inside its protection band and rests as a limit order, a market
order stopped by that band, an order refused for sitting outside the day's price
band, and a self-trade prevented.
"""

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from pricetime.commands import (
    CancelOrder,
    Command,
    ModifyOrder,
    NewLimitOrder,
    NewMarketOrder,
    Validity,
)
from pricetime.journal import JournaledEngine
from pricetime.orders import SelfTradeAction, Side
from pricetime.rules import MarketRules, PriceBand
from pricetime.viewer.render import Session, write

SYMBOL = "DEMOCO"
TICK_SIZE = Decimal("0.05")

# A stock that closed at 100.00 and may trade between 90.00 and 110.00 today.
# Market orders may reach 5% from the last trade, and never less than 2 ticks.
DEMO_RULES = MarketRules(
    price_band=PriceBand(lower=1_800, upper=2_200),
    protection_bps=500,
    protection_min_ticks=2,
    opening_price=2_000,
)

PATIENT = 1
CROSSER = 2
MAKER = 3
RIVAL = 4


@dataclass(frozen=True, slots=True)
class Step:
    """One command and the client that sent it, for the journal's annotation."""

    command: Command
    client_order_id: str


def limit(
    side: Side,
    price: int,
    quantity: int,
    client: int,
    validity: Validity = Validity.DAY,
) -> NewLimitOrder:
    """A limit order, written short so the script below reads as a script."""
    return NewLimitOrder(
        side=side,
        price=price,
        quantity=quantity,
        client_id=client,
        validity=validity,
    )


def script() -> list[Step]:
    """The session, in order."""
    steps: list[Command] = [
        # A book builds up on both sides.
        limit(Side.BUY, 1_995, 50, PATIENT),
        limit(Side.BUY, 1_990, 80, PATIENT),
        limit(Side.BUY, 1_985, 120, RIVAL),
        limit(Side.SELL, 2_005, 60, MAKER),
        limit(Side.SELL, 2_010, 90, MAKER),
        limit(Side.SELL, 2_020, 150, RIVAL),
        # A buyer sweeps two levels and rests what is left.
        limit(Side.BUY, 2_010, 200, CROSSER),
        # Shrinking in place keeps the queue position; repricing loses it.
        ModifyOrder(order_id=1, price=1_995, quantity=30),
        ModifyOrder(order_id=2, price=1_995, quantity=80),
        limit(Side.BUY, 1_995, 40, RIVAL),
        # Immediate or cancel: take what is there, cancel the rest.
        limit(Side.SELL, 1_995, 240, MAKER, validity=Validity.IOC),
        # The last offer is pulled, so a market order empties the book inside its
        # protection band and what is left rests as a limit order.
        limit(Side.SELL, 2_005, 20, MAKER),
        CancelOrder(order_id=6),
        NewMarketOrder(side=Side.BUY, quantity=60, client_id=CROSSER),
        # The only offer left is beyond the protection band, so the next market
        # order trades nothing at all rather than paying any price for it.
        limit(Side.SELL, 2_120, 100, RIVAL),
        NewMarketOrder(side=Side.BUY, quantity=80, client_id=CROSSER),
        # An order priced outside the day's price band is refused.
        limit(Side.BUY, 2_400, 10, RIVAL),
        # A client meets its own order, and self-trade prevention stops the trade.
        # The incoming order is the one cancelled, which is the default.
        limit(Side.SELL, 2_015, 40, PATIENT),
        limit(Side.BUY, 2_015, 40, PATIENT),
        # The same meeting again, this time cancelling the resting order instead.
        NewLimitOrder(
            side=Side.BUY,
            price=2_015,
            quantity=40,
            client_id=PATIENT,
            self_trade=SelfTradeAction.CANCEL_PASSIVE,
        ),
        # A quiet cancel to finish.
        CancelOrder(order_id=3),
    ]
    return [Step(command, f"DEMO-{number}") for number, command in enumerate(steps, start=1)]


def record(journal: Path) -> Path:
    """Run the session and write its journal."""
    journal.parent.mkdir(parents=True, exist_ok=True)
    if journal.exists():
        journal.unlink()
    with JournaledEngine(journal, DEMO_RULES) as engine:
        for step in script():
            engine.process(
                step.command,
                {"session": "DEMO", "clordid": step.client_order_id, "account": "DEMO-ACC"},
            )
    return journal


def main(argv: Sequence[str] | None = None) -> int:
    """Record the demo session and build the page that shows it."""
    parser = argparse.ArgumentParser(description="Record a demo session and build its page.")
    parser.add_argument("--journal", type=Path, default=Path("docs/demo/session.jsonl"))
    parser.add_argument("--output", "-o", type=Path, default=Path("docs/index.html"))
    arguments = parser.parse_args(argv)

    journal = record(arguments.journal)
    page = write(
        journal,
        arguments.output,
        Session(symbol=SYMBOL, tick_size=TICK_SIZE),
    )
    sys.stdout.write(f"journal: {journal}\npage: {page}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
