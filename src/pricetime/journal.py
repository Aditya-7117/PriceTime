"""The journal: an append-only record of every command, and replay from it.

The journal is the engine's write-ahead log. Each command is written and flushed
before the engine applies it, including commands the engine will reject, because
a rejected new order still uses up an order ID. The header records the market
rules the engine ran under. Replaying the journal into a fresh engine therefore
rebuilds the same book, the same IDs and the same events.

The line format lives in `pricetime.codec`. This module owns the file.
"""

import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import IO, Self

from pricetime.codec import (
    DecodeError,
    decode_command,
    decode_header,
    encode_command,
    encode_header,
)
from pricetime.commands import Command
from pricetime.engine import MatchingEngine
from pricetime.events import Event
from pricetime.rules import MarketRules

logger = logging.getLogger(__name__)


type Annotation = dict[str, str] | None


@dataclass(frozen=True, slots=True)
class JournalRecord:
    """One line of the journal: a command, its number, and who it belongs to."""

    sequence: int
    command: Command
    annotation: Annotation = None


class JournalError(Exception):
    """The journal is damaged, unreadable, mismatched or already closed."""


class JournalWriter:
    """Appends commands to a journal file and flushes each one as it is written.

    Opening an existing journal reads it end to end first. That checks every
    record before anything new goes after it, confirms the journal was written
    under the same market rules, and finds the next sequence number. Existing
    records are never rewritten.
    """

    def __init__(self, path: Path, rules: MarketRules) -> None:
        existing = read_rules(path) if path.exists() else None
        if existing is not None and existing != rules:
            raise JournalError(f"{path} was written under different market rules: {existing}")
        self._next_sequence = 1 + sum(1 for _ in read_journal(path)) if existing is not None else 1
        self._file: IO[str] = path.open("a", encoding="utf-8")
        if self._file.tell() == 0:
            self._write_line(encode_header(rules))

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def append(self, command: Command, annotation: Annotation = None) -> int:
        """Write one command and flush it to the operating system.

        Args:
            command: The command to record.
            annotation: What the layer above the engine needs to rebuild its own
                state from this journal. The engine ignores it.

        Returns:
            The command's sequence number.

        Raises:
            JournalError: If the journal has been closed.
        """
        if self._file.closed:
            raise JournalError("journal is closed")
        sequence = self._next_sequence
        self._write_line(encode_command(sequence, command, annotation))
        self._next_sequence += 1
        return sequence

    def sync(self) -> None:
        """Force everything written so far onto the disk.

        The gateway calls this once per batch, before any acknowledgement goes
        out, so nothing is admitted to a client that the disk does not hold.
        """
        if self._file.closed:
            return
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self) -> None:
        """Force everything written to disk, then close. Safe to call more than once."""
        if self._file.closed:
            return
        self.sync()
        self._file.close()

    def _write_line(self, line: str) -> None:
        self._file.write(line + "\n")
        self._file.flush()


def read_rules(path: Path) -> MarketRules | None:
    """The market rules in a journal's header, or None for an empty file.

    Raises:
        JournalError: If the header is damaged or not a header at all.
    """
    with path.open(encoding="utf-8") as file:
        return _header_rules(file.readline())


def read_journal(path: Path) -> Iterator[JournalRecord]:
    """Yield the records in a journal in order, checking every one on the way.

    An empty file is an empty journal: nothing was ever recorded.

    Raises:
        JournalError: On a damaged header, a malformed record, a gap in the
            sequence numbers, or a final record cut off mid-write.
    """
    with path.open(encoding="utf-8") as file:
        if _header_rules(file.readline()) is None:
            return
        expected = 1
        for line_number, line in enumerate(file, start=2):
            where = f"line {line_number}"
            if not line.endswith("\n"):
                raise JournalError(f"{where}: incomplete final record, cut off mid-write")
            try:
                sequence, command, annotation = decode_command(line)
            except DecodeError as error:
                raise JournalError(f"{where}: {error}") from error
            if sequence != expected:
                raise JournalError(f"{where}: expected sequence {expected}, found {sequence}")
            expected += 1
            yield JournalRecord(sequence=sequence, command=command, annotation=annotation)


def replay(path: Path) -> MatchingEngine:
    """Rebuild an engine under the journal's rules by applying every command in order.

    Raises:
        JournalError: If the journal has no header, or is damaged.
    """
    rules = read_rules(path)
    if rules is None:
        raise JournalError(f"{path} has no header, so there is nothing to replay")
    engine = MatchingEngine(rules)
    for record in read_journal(path):
        engine.process(record.command)
    return engine


class JournaledEngine:
    """A matching engine that records every command in a journal before applying it.

    Opening an existing journal replays it, so a restarted engine carries on
    from exactly where the last one stopped. A command that cannot be recorded
    is not applied, so the engine is never ahead of its journal.
    """

    def __init__(self, path: Path, rules: MarketRules) -> None:
        self._writer = JournalWriter(path, rules)
        self._engine = replay(path)
        recovered = self._engine.snapshot().sequence
        if recovered:
            logger.info(
                "recovered %d commands from %s",
                recovered,
                path,
                extra={"journal": str(path), "commands": recovered},
            )

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

    def process(self, command: Command, annotation: Annotation = None) -> list[Event]:
        """Record a command, then apply it and return the events it caused.

        Args:
            command: The command to apply.
            annotation: Anything the caller needs to rebuild its own state from
                the journal later. The engine ignores it.

        Raises:
            JournalError: If the journal has been closed. The command is not applied.
        """
        self._writer.append(command, annotation)
        return self._engine.process(command)

    def sync(self) -> None:
        """Force every command recorded so far onto the disk."""
        self._writer.sync()

    def close(self) -> None:
        """Close the journal. Commands can no longer be processed."""
        self._writer.close()


def _header_rules(line: str) -> MarketRules | None:
    if not line:
        return None
    if not line.endswith("\n"):
        raise JournalError("line 1: incomplete header, cut off mid-write")
    try:
        return decode_header(line)
    except DecodeError as error:
        raise JournalError(f"line 1: {error}") from error
