"""Where a FIX session keeps its sequence numbers and what it has sent.

A session that cannot repeat a message it sent cannot answer a resend request,
so the store is part of the protocol rather than a convenience. Two
implementations live here: one in memory, for tests and short-lived clients, and
one backed by an append-only file, so a restarted exchange can still answer for
what it sent before.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import IO, Protocol, Self

from pricetime.fix.wire import Fields


@dataclass(frozen=True, slots=True)
class StoredMessage:
    """A message this session sent, kept so it can be repeated exactly.

    The body is everything after the session header, because a repeat carries a
    fresh header with the original sequence number, `PossDupFlag` and the
    original sending time.
    """

    sequence: int
    msg_type: str
    body: Fields
    sending_time: str
    admin: bool


class SessionStore(Protocol):
    """What a session needs to remember between messages, and across restarts."""

    @property
    def next_outbound(self) -> int:
        """The sequence number the next message this side sends will carry."""

    @property
    def next_inbound(self) -> int:
        """The sequence number this side expects to receive next."""

    def record_outbound(self, message: StoredMessage) -> None:
        """Remember a message this side sent, so it can be repeated."""

    def record_inbound(self, sequence: int) -> None:
        """Note that this side has processed an incoming message."""

    def expect_inbound(self, sequence: int) -> None:
        """Move the expected incoming number forward, after a sequence reset."""

    def between(self, begin: int, end: int) -> list[StoredMessage]:
        """Sent messages from `begin` to `end`, both included, in order."""

    def reset(self) -> None:
        """Start both sequence numbers again from one."""

    def sync(self) -> None:
        """Force everything recorded so far onto the disk, if there is one."""


class InMemoryStore:
    """A store that forgets everything when the process stops."""

    def __init__(self) -> None:
        self._sent: dict[int, StoredMessage] = {}
        self._next_outbound = 1
        self._next_inbound = 1

    @property
    def next_outbound(self) -> int:
        """The sequence number the next message this side sends will carry."""
        return self._next_outbound

    @property
    def next_inbound(self) -> int:
        """The sequence number this side expects to receive next."""
        return self._next_inbound

    def record_outbound(self, message: StoredMessage) -> None:
        """Remember a message this side sent, so it can be repeated."""
        self._sent[message.sequence] = message
        self._next_outbound = message.sequence + 1

    def record_inbound(self, sequence: int) -> None:
        """Note that this side has processed an incoming message."""
        self._next_inbound = sequence + 1

    def expect_inbound(self, sequence: int) -> None:
        """Move the expected incoming number forward, after a sequence reset."""
        self._next_inbound = sequence

    def between(self, begin: int, end: int) -> list[StoredMessage]:
        """Sent messages from `begin` to `end`, both included, in order."""
        return [self._sent[s] for s in range(begin, end + 1) if s in self._sent]

    def reset(self) -> None:
        """Start both sequence numbers again from one."""
        self._sent.clear()
        self._next_outbound = 1
        self._next_inbound = 1

    def sync(self) -> None:
        """Nothing to force to disk."""


class FileStore:
    """A store on disk, so sequence numbers and sent messages survive a restart.

    One JSON object per line, appended and flushed as it happens, exactly like
    the engine's journal. A restart reads the file back and carries on with the
    next sequence number, which is what lets a counterparty ask for messages
    sent before the restart.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._sent: dict[int, StoredMessage] = {}
        self._next_outbound = 1
        self._next_inbound = 1
        if path.exists():
            self._load()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file: IO[str] = path.open("a", encoding="utf-8")

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
    def next_outbound(self) -> int:
        """The sequence number the next message this side sends will carry."""
        return self._next_outbound

    @property
    def next_inbound(self) -> int:
        """The sequence number this side expects to receive next."""
        return self._next_inbound

    def record_outbound(self, message: StoredMessage) -> None:
        """Remember a message this side sent, so it can be repeated."""
        self._sent[message.sequence] = message
        self._next_outbound = message.sequence + 1
        self._write(
            {
                "out": message.sequence,
                "type": message.msg_type,
                "body": [[tag, value] for tag, value in message.body],
                "time": message.sending_time,
                "admin": message.admin,
            }
        )

    def record_inbound(self, sequence: int) -> None:
        """Note that this side has processed an incoming message."""
        self._next_inbound = sequence + 1
        self._write({"in": sequence})

    def expect_inbound(self, sequence: int) -> None:
        """Move the expected incoming number forward, after a sequence reset."""
        self._next_inbound = sequence
        self._write({"expect": sequence})

    def between(self, begin: int, end: int) -> list[StoredMessage]:
        """Sent messages from `begin` to `end`, both included, in order."""
        return [self._sent[s] for s in range(begin, end + 1) if s in self._sent]

    def reset(self) -> None:
        """Start both sequence numbers again from one."""
        self._sent.clear()
        self._next_outbound = 1
        self._next_inbound = 1
        self._write({"reset": True})

    def sync(self) -> None:
        """Force what has been written onto the disk."""
        self._file.flush()
        os.fsync(self._file.fileno())

    def close(self) -> None:
        """Force everything to disk and close the file. Safe to call more than once."""
        if self._file.closed:
            return
        self.sync()
        self._file.close()

    def _write(self, record: dict[str, object]) -> None:
        self._file.write(json.dumps(record, separators=(",", ":")) + "\n")
        self._file.flush()

    def _load(self) -> None:
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line:
                continue
            record = json.loads(line)
            if record.get("reset"):
                self._sent.clear()
                self._next_outbound = 1
                self._next_inbound = 1
            elif "out" in record:
                message = StoredMessage(
                    sequence=int(record["out"]),
                    msg_type=str(record["type"]),
                    body=tuple((int(tag), str(value)) for tag, value in record["body"]),
                    sending_time=str(record["time"]),
                    admin=bool(record["admin"]),
                )
                self._sent[message.sequence] = message
                self._next_outbound = message.sequence + 1
            elif "in" in record:
                self._next_inbound = int(record["in"]) + 1
            elif "expect" in record:
                self._next_inbound = int(record["expect"])
