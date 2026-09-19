"""The journal: an append-only record of every command, and replay from it.

The journal is the engine's write-ahead log. Each command is written and flushed
before the engine applies it, including commands the engine will reject, because
a rejected new order still uses up an order ID. Replaying the journal into a
fresh engine therefore rebuilds the same book, the same IDs and the same events.

The file holds one JSON object per line. The first line names the format and its
version. Every later line is one command, numbered from 1 with no gaps.
"""

import json
import logging
import os
from collections.abc import Callable, Iterator
from pathlib import Path
from types import TracebackType
from typing import IO, Self, assert_never

from pricetime.commands import CancelOrder, Command, ModifyOrder, NewLimitOrder, NewMarketOrder
from pricetime.engine import MatchingEngine
from pricetime.events import Event
from pricetime.orders import Side

logger = logging.getLogger(__name__)

FORMAT = "pricetime-journal"
VERSION = 1
_HEADER = json.dumps({"format": FORMAT, "version": VERSION}, separators=(",", ":"))

type _Record = dict[str, object]


class JournalError(Exception):
    """The journal is damaged, unreadable, or already closed."""


class JournalWriter:
    """Appends commands to a journal file and flushes each one as it is written.

    Opening an existing journal reads it end to end first. That checks every
    record before anything new goes after it, and finds the next sequence
    number. Existing records are never rewritten.
    """

    def __init__(self, path: Path) -> None:
        self._next_sequence = 1 + sum(1 for _ in read_journal(path)) if path.exists() else 1
        self._file: IO[str] = path.open("a", encoding="utf-8")
        if self._file.tell() == 0:
            self._write_line(_HEADER)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def append(self, command: Command) -> int:
        """Write one command and flush it to the operating system.

        Returns:
            The command's sequence number.

        Raises:
            JournalError: If the journal has been closed.
        """
        if self._file.closed:
            raise JournalError("journal is closed")
        sequence = self._next_sequence
        self._write_line(_encode(sequence, command))
        self._next_sequence += 1
        return sequence

    def close(self) -> None:
        """Force everything written to disk, then close. Safe to call more than once."""
        if self._file.closed:
            return
        self._file.flush()
        os.fsync(self._file.fileno())
        self._file.close()

    def _write_line(self, line: str) -> None:
        self._file.write(line + "\n")
        self._file.flush()


def read_journal(path: Path) -> Iterator[Command]:
    """Yield the commands in a journal in order, checking every record on the way.

    An empty file is an empty journal: nothing was ever recorded.

    Raises:
        JournalError: On an unknown header, a malformed record, a gap in the
            sequence numbers, or a final record cut off mid-write.
    """
    with path.open(encoding="utf-8") as file:
        header = file.readline()
        if not header:
            return
        _check_header(header)
        expected = 1
        for line_number, line in enumerate(file, start=2):
            where = f"line {line_number}"
            if not line.endswith("\n"):
                raise JournalError(f"{where}: incomplete final record, cut off mid-write")
            sequence, command = _decode(line, where)
            if sequence != expected:
                raise JournalError(f"{where}: expected sequence {expected}, found {sequence}")
            expected += 1
            yield command


def replay(path: Path) -> MatchingEngine:
    """Rebuild an engine by applying every command in a journal, in order."""
    engine = MatchingEngine()
    for command in read_journal(path):
        engine.process(command)
    return engine


class JournaledEngine:
    """A matching engine that records every command in a journal before applying it.

    Opening an existing journal replays it first, so a restarted engine carries
    on from exactly where the last one stopped. A command that cannot be
    recorded is not applied, so the engine is never ahead of its journal.
    """

    def __init__(self, path: Path) -> None:
        self._engine = replay(path) if path.exists() else MatchingEngine()
        recovered = self._engine.snapshot().sequence
        if recovered:
            logger.info(
                "recovered %d commands from %s",
                recovered,
                path,
                extra={"journal": str(path), "commands": recovered},
            )
        self._writer = JournalWriter(path)

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    @property
    def engine(self) -> MatchingEngine:
        """The engine behind the journal, for inspection."""
        return self._engine

    def process(self, command: Command) -> list[Event]:
        """Record a command, then apply it and return the events it caused.

        Raises:
            JournalError: If the journal has been closed. The command is not applied.
        """
        self._writer.append(command)
        return self._engine.process(command)

    def close(self) -> None:
        """Close the journal. Commands can no longer be processed."""
        self._writer.close()


def _encode(sequence: int, command: Command) -> str:
    record: _Record
    match command:
        case NewLimitOrder():
            record = {
                "seq": sequence,
                "type": "limit",
                "side": command.side.value,
                "price": command.price,
                "quantity": command.quantity,
            }
        case NewMarketOrder():
            record = {
                "seq": sequence,
                "type": "market",
                "side": command.side.value,
                "quantity": command.quantity,
            }
        case CancelOrder():
            record = {"seq": sequence, "type": "cancel", "order_id": command.order_id}
        case ModifyOrder():
            record = {
                "seq": sequence,
                "type": "modify",
                "order_id": command.order_id,
                "price": command.price,
                "quantity": command.quantity,
            }
        case _:
            assert_never(command)
    return json.dumps(record, separators=(",", ":"))


def _check_header(line: str) -> None:
    try:
        header = json.loads(line)
    except json.JSONDecodeError as error:
        raise JournalError("line 1: not a pricetime journal") from error
    if not isinstance(header, dict) or header.get("format") != FORMAT:
        raise JournalError("line 1: not a pricetime journal")
    if header.get("version") != VERSION:
        raise JournalError(f"line 1: unsupported version {header.get('version')!r}")


def _decode(line: str, where: str) -> tuple[int, Command]:
    try:
        record = json.loads(line)
    except json.JSONDecodeError as error:
        raise JournalError(f"{where}: not valid JSON") from error
    if not isinstance(record, dict):
        raise JournalError(f"{where}: not a JSON object")
    kind = record.get("type")
    if not isinstance(kind, str) or kind not in _DECODERS:
        raise JournalError(f"{where}: unknown command type {kind!r}")
    fields, build = _DECODERS[kind]
    expected = {"seq", "type", *fields}
    if set(record) != expected:
        raise JournalError(f"{where}: expected fields {sorted(expected)}, found {sorted(record)}")
    return _integer(record, "seq", where), build(record, where)


def _integer(record: _Record, key: str, where: str) -> int:
    value = record[key]
    # bool is a subclass of int in Python, and true is not a quantity.
    if type(value) is not int:
        raise JournalError(f"{where}: {key} must be an integer, found {value!r}")
    return value


def _side(record: _Record, where: str) -> Side:
    value = record["side"]
    try:
        return Side(value)
    except ValueError as error:
        raise JournalError(f"{where}: side must be 'buy' or 'sell', found {value!r}") from error


def _limit(record: _Record, where: str) -> Command:
    return NewLimitOrder(
        side=_side(record, where),
        price=_integer(record, "price", where),
        quantity=_integer(record, "quantity", where),
    )


def _market(record: _Record, where: str) -> Command:
    return NewMarketOrder(side=_side(record, where), quantity=_integer(record, "quantity", where))


def _cancel(record: _Record, where: str) -> Command:
    return CancelOrder(order_id=_integer(record, "order_id", where))


def _modify(record: _Record, where: str) -> Command:
    return ModifyOrder(
        order_id=_integer(record, "order_id", where),
        price=_integer(record, "price", where),
        quantity=_integer(record, "quantity", where),
    )


_DECODERS: dict[str, tuple[tuple[str, ...], Callable[[_Record, str], Command]]] = {
    "limit": (("side", "price", "quantity"), _limit),
    "market": (("side", "quantity"), _market),
    "cancel": (("order_id",), _cancel),
    "modify": (("order_id", "price", "quantity"), _modify),
}
