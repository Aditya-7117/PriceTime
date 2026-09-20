"""Latency measurement, and the method behind the numbers.

Two things are measured. **Service time** is how long the engine takes to apply
one command. **Response time** is how long an order waited from the moment it
was *due* to be sent until it was done, which includes any queueing when the
engine cannot keep up with the schedule.

Orders go out on a fixed schedule set before the run, and every latency is
counted from that scheduled moment. This is what avoids coordinated omission: in
a closed loop, where the next order waits for the previous reply, a slow engine
quietly slows the load generator down with it, so the stall never appears in the
numbers and the percentiles come out flattering. Here the schedule does not
care how slow the engine is, so a stall shows up in every order that was due
during it, which is what a real client would feel.

Warm-up is thrown away: the first orders pay for an empty book, cold caches and
a young interpreter, and are not part of the steady state being measured.

Run it with `python -m pricetime.bench`.
"""

import argparse
import asyncio
import gc
import json
import platform
import random
import subprocess
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from pricetime.commands import CancelOrder, Command, ModifyOrder, NewLimitOrder, NewMarketOrder
from pricetime.engine import MatchingEngine
from pricetime.fix import tags
from pricetime.fix.client import ClientSettings, ExchangeClient
from pricetime.fix.config import ExchangeConfig, Instrument, SessionSettings
from pricetime.fix.server import ExchangeServer
from pricetime.fix.tags import FixSide, MsgType, OrdType, TimeInForce
from pricetime.orders import Side
from pricetime.rules import MarketRules, PriceBand

NANOSECONDS = 1_000_000_000
READ_SIZE = 65536
SPIN_THRESHOLD_NS = 200_000  # sleep until this close to the deadline, then spin
PERCENTILES = (0.50, 0.99, 0.999)

# The shape of the synthetic load: mostly resting limit orders, with a few
# market orders, cancels and modifies mixed in, as a real book sees.
LIMIT_SHARE = 0.70
MARKET_SHARE = 0.75
CANCEL_SHARE = 0.90
BUY_SHARE = 0.5

SYMBOL = "DEMOCO"
TICK_SIZE = Decimal("0.05")
EXCHANGE_ID = "PRICETIME"
CLIENT_ID = "BENCHCLIENT"
ACCOUNT = "ACC-1"

BENCH_RULES = MarketRules(
    price_band=PriceBand(lower=1_500, upper=2_500),
    protection_bps=500,
    protection_min_ticks=2,
    opening_price=2_000,
)


@dataclass(slots=True)
class Measurement:
    """Every latency sample from one run, in nanoseconds."""

    name: str
    service: list[int] = field(default_factory=list)
    response: list[int] = field(default_factory=list)

    def percentiles(self, samples: Sequence[int]) -> dict[str, float]:
        """Exact percentiles, in microseconds, from the samples themselves."""
        ordered = sorted(samples)
        return {
            f"p{fraction * 100:g}": _microseconds(_percentile(ordered, fraction))
            for fraction in PERCENTILES
        } | {"max": _microseconds(ordered[-1]) if ordered else 0.0}

    def report(self) -> dict[str, object]:
        """What was measured, ready to print or to write out."""
        return {
            "name": self.name,
            "samples": len(self.response),
            "service_time_us": self.percentiles(self.service),
            "response_time_us": self.percentiles(self.response),
        }


def _percentile(ordered: Sequence[int], fraction: float) -> int:
    if not ordered:
        return 0
    index = min(len(ordered) - 1, int(fraction * len(ordered)))
    return ordered[index]


def _microseconds(nanoseconds: int) -> float:
    return round(nanoseconds / 1_000, 2)


def schedule(rate: float, count: int) -> Iterator[tuple[int, int]]:
    """Yield (index, due time in nanoseconds) on a fixed schedule from now."""
    start = time.perf_counter_ns()
    step = NANOSECONDS / rate
    for index in range(count):
        yield index, start + int(index * step)


def wait_until(due: int) -> None:
    """Wait for a deadline: sleep for the bulk of it, then spin for accuracy."""
    while True:
        remaining = due - time.perf_counter_ns()
        if remaining <= 0:
            return
        if remaining > SPIN_THRESHOLD_NS:
            time.sleep((remaining - SPIN_THRESHOLD_NS) / NANOSECONDS)


async def wait_until_async(due: int) -> None:
    """Wait for a deadline without blocking the event loop.

    The blocking version would freeze everything else, including the task
    reading replies, and every reply would then look slow for a reason that has
    nothing to do with the exchange.
    """
    while True:
        remaining = due - time.perf_counter_ns()
        if remaining <= 0:
            return
        await asyncio.sleep(remaining / NANOSECONDS if remaining > SPIN_THRESHOLD_NS else 0)


def synthetic_commands(seed: int, count: int) -> list[Command]:
    """A reproducible stream of orders around a mid price, with cancels and modifies."""
    rng = random.Random(seed)  # noqa: S311 - a reproducible load shape, not a secret
    commands: list[Command] = []
    issued = 0
    for _ in range(count):
        roll = rng.random()
        side = Side.BUY if rng.random() < BUY_SHARE else Side.SELL
        client = rng.randint(1, 4)
        quantity = rng.randint(1, 50)
        if roll < LIMIT_SHARE:
            drift = rng.randint(-8, 8)
            price = 2_000 + drift if side is Side.BUY else 2_002 + drift
            commands.append(
                NewLimitOrder(side=side, price=price, quantity=quantity, client_id=client)
            )
            issued += 1
        elif roll < MARKET_SHARE:
            commands.append(NewMarketOrder(side=side, quantity=quantity, client_id=client))
            issued += 1
        elif roll < CANCEL_SHARE:
            commands.append(CancelOrder(order_id=rng.randint(1, max(1, issued))))
        else:
            commands.append(
                ModifyOrder(
                    order_id=rng.randint(1, max(1, issued)),
                    price=2_000 + rng.randint(-8, 8),
                    quantity=quantity,
                )
            )
    return commands


def measure_engine(
    *, rate: float, seconds: float, warmup: float, seed: int = 1, collect_garbage: bool = True
) -> Measurement:
    """Drive the engine alone, in this process, on a fixed schedule.

    With `collect_garbage` off, the collector is paused for the steady state.
    Nothing in production should run that way for long, but the pair of runs
    shows how much of the tail is the collector and how much is the engine.
    """
    total = int(rate * (seconds + warmup))
    warmup_count = int(rate * warmup)
    commands = synthetic_commands(seed, total)
    engine = MatchingEngine(BENCH_RULES)
    collector = "collector on" if collect_garbage else "collector paused"
    measurement = Measurement(name=f"engine at {rate:g}/s, {collector}")

    try:
        for index, due in schedule(rate, total):
            wait_until(due)
            if index == warmup_count and not collect_garbage:
                gc.collect()
                gc.disable()
            started = time.perf_counter_ns()
            engine.process(commands[index])
            finished = time.perf_counter_ns()
            if index >= warmup_count:
                measurement.service.append(finished - started)
                measurement.response.append(finished - due)
    finally:
        gc.enable()
    return measurement


async def measure_fix(
    *, rate: float, seconds: float, warmup: float, force_to_disk: bool = True
) -> Measurement:
    """Drive the whole exchange over a loopback socket: order in, report out."""
    total = int(rate * (seconds + warmup))
    warmup_count = int(rate * warmup)
    durability = "forced to disk" if force_to_disk else "no disk sync"
    measurement = Measurement(name=f"FIX round trip at {rate:g}/s, {durability}")

    with TemporaryDirectory() as directory:
        workspace = Path(directory)
        server = ExchangeServer(_bench_config(workspace, force_to_disk=force_to_disk))
        await server.start()
        client = ExchangeClient(
            "127.0.0.1",
            server.port,
            ClientSettings(comp_id=CLIENT_ID, target_comp_id=EXCHANGE_ID, heartbeat_interval=30),
        )
        try:
            await client.logon()
            outstanding = _Outstanding()
            reader = asyncio.create_task(
                _collect(client, measurement, outstanding, total, warmup_count)
            )
            for index, due in schedule(rate, total):
                await wait_until_async(due)
                client_order_id = f"B{index}"
                outstanding.record(client_order_id, due)
                await client.send(_bench_order(client_order_id, index))
            await asyncio.wait_for(reader, timeout=30)
        finally:
            with suppress(TimeoutError, ConnectionError):
                await client.close()
            await server.close()
    return measurement


@dataclass(slots=True)
class _Outstanding:
    """When each order was due and when it actually went out."""

    due: dict[str, int] = field(default_factory=dict)
    sent: dict[str, int] = field(default_factory=dict)

    def record(self, client_order_id: str, due: int) -> None:
        self.due[client_order_id] = due
        self.sent[client_order_id] = time.perf_counter_ns()


async def measure_echo(*, rate: float, seconds: float, warmup: float) -> Measurement:
    """The floor: an empty asyncio echo over the same loopback socket.

    Without this, a round-trip number says nothing. Whatever the exchange costs,
    it costs on top of what Python's event loop and the operating system charge
    to carry a few hundred bytes to another socket and back on this machine.
    """
    total = int(rate * (seconds + warmup))
    warmup_count = int(rate * warmup)
    measurement = Measurement(name=f"baseline: bare asyncio echo at {rate:g}/s")

    async def echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            data = await reader.read(READ_SIZE)
            if not data:
                return
            writer.write(data)
            await writer.drain()

    server = await asyncio.start_server(echo, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        for index, due in schedule(rate, total):
            await wait_until_async(due)
            sent = time.perf_counter_ns()
            writer.write(b"x" * 200)
            await writer.drain()
            await reader.read(READ_SIZE)
            arrived = time.perf_counter_ns()
            if index >= warmup_count:
                measurement.service.append(arrived - sent)
                measurement.response.append(arrived - due)
    finally:
        writer.close()
        server.close()
    return measurement


async def _collect(
    client: ExchangeClient,
    measurement: Measurement,
    outstanding: _Outstanding,
    total: int,
    warmup_count: int,
) -> None:
    """Match each execution report back to the order that caused it."""
    seen = 0
    while seen < total:
        report = dict(await client.report(timeout=30))
        client_order_id = report.get(tags.CL_ORD_ID, "")
        if client_order_id not in outstanding.due:
            continue
        arrived = time.perf_counter_ns()
        if int(client_order_id[1:]) >= warmup_count:
            measurement.service.append(arrived - outstanding.sent[client_order_id])
            measurement.response.append(arrived - outstanding.due[client_order_id])
        seen += 1


def _bench_order(client_order_id: str, index: int) -> tuple[tuple[int, str], ...]:
    side = FixSide.BUY if index % 2 else FixSide.SELL
    price = "100.00" if index % 2 else "100.05"
    return (
        (tags.MSG_TYPE, MsgType.NEW_ORDER_SINGLE),
        (tags.CL_ORD_ID, client_order_id),
        (tags.ACCOUNT, ACCOUNT),
        (tags.SYMBOL, SYMBOL),
        (tags.SIDE, side),
        (tags.ORDER_QTY, "10"),
        (tags.ORD_TYPE, OrdType.LIMIT),
        (tags.PRICE, price),
        (tags.TIME_IN_FORCE, TimeInForce.DAY),
        (tags.TRANSACT_TIME, "20260921-09:15:00.000"),
    )


def _bench_config(workspace: Path, *, force_to_disk: bool = True) -> ExchangeConfig:
    return ExchangeConfig(
        comp_id=EXCHANGE_ID,
        force_to_disk=force_to_disk,
        instruments={
            SYMBOL: Instrument(
                symbol=SYMBOL,
                journal=workspace / "journal.jsonl",
                rules=BENCH_RULES,
                tick_size=TICK_SIZE,
            )
        },
        accounts={ACCOUNT: 1},
        sessions={CLIENT_ID: SessionSettings(comp_id=CLIENT_ID, store=workspace / "session.jsonl")},
    )


def environment() -> dict[str, object]:
    """Everything a reader needs to judge the numbers."""
    return {
        "measured": datetime.now(UTC).isoformat(timespec="seconds"),
        "machine": f"{platform.machine()} {platform.processor()}".strip(),
        "system": f"{platform.system()} {platform.release()}",
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "commit": _commit(),
        "garbage_collector": "enabled" if gc.isenabled() else "disabled",
    }


def _commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 - git is on PATH by design
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return "unknown"
    return result.stdout.strip()


def render(results: list[dict[str, object]], where: dict[str, object]) -> str:
    """The numbers and the method, as text to paste into a write-up."""
    lines = ["PriceTime latency", ""]
    lines += [f"{key}: {value}" for key, value in where.items()]
    lines.append("")
    lines.append(f"{'measurement':<58}{'samples':>9}{'p50':>9}{'p99':>9}{'p99.9':>9}{'max':>9}")
    lines.append("-" * 103)
    for result in results:
        name = str(result["name"])
        for kind in ("service_time_us", "response_time_us"):
            values = result[kind]
            assert isinstance(values, dict)  # noqa: S101 - built above, checked for the reader
            label = f"{name}, {kind.removesuffix('_us').replace('_', ' ')}"
            lines.append(
                f"{label:<58}{result['samples']:>9}"
                f"{values['p50']:>9}{values['p99']:>9}{values['p99.9']:>9}{values['max']:>9}"
            )
    lines.append("")
    lines.append("All figures are microseconds. Service time is the work itself; response time is")
    lines.append("measured from the moment each order was due, so a stall is counted against every")
    lines.append("order that was waiting for it.")
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the benchmarks and print the numbers with their method."""
    parser = argparse.ArgumentParser(description="Measure PriceTime's latency.")
    parser.add_argument("--rate", type=float, default=20_000, help="orders per second (engine)")
    parser.add_argument("--fix-rate", type=float, default=2_000, help="orders per second (FIX)")
    parser.add_argument("--seconds", type=float, default=3.0, help="steady state, in seconds")
    parser.add_argument("--warmup", type=float, default=1.0, help="warm-up, in seconds")
    parser.add_argument("--engine-only", action="store_true", help="skip the FIX round trip")
    parser.add_argument("--json", type=Path, default=None, help="also write the results here")
    arguments = parser.parse_args(argv)

    results = [
        measure_engine(
            rate=arguments.rate,
            seconds=arguments.seconds,
            warmup=arguments.warmup,
            collect_garbage=collect_garbage,
        ).report()
        for collect_garbage in (True, False)
    ]
    if not arguments.engine_only:
        results.append(
            asyncio.run(
                measure_echo(
                    rate=arguments.fix_rate, seconds=arguments.seconds, warmup=arguments.warmup
                )
            ).report()
        )
    if not arguments.engine_only:
        results.extend(
            asyncio.run(
                measure_fix(
                    rate=arguments.fix_rate,
                    seconds=arguments.seconds,
                    warmup=arguments.warmup,
                    force_to_disk=force_to_disk,
                )
            ).report()
            for force_to_disk in (True, False)
        )
    where = environment()
    sys.stdout.write(render(results, where) + "\n")
    if arguments.json is not None:
        arguments.json.write_text(
            json.dumps({"environment": where, "results": results}, indent=2) + "\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
