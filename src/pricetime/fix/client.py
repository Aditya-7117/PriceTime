"""A FIX client: the counterparty side of the conversation.

The same session logic the exchange runs, pointed the other way, with a socket
attached. The demo and the benchmark use it, and so do the tests, which is the
point: the simulated counterparty and a real client are the same code.
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Self

from pricetime.fix.session import (
    Action,
    Deliver,
    Disconnect,
    FixSession,
    LoggedOn,
    LoggedOut,
    Role,
    Send,
    SessionId,
)
from pricetime.fix.store import FileStore, InMemoryStore, SessionStore
from pricetime.fix.wire import Fields

logger = logging.getLogger(__name__)

READ_SIZE = 65536
TICK_SECONDS = 1.0


@dataclass(frozen=True, slots=True)
class ClientSettings:
    """Who this client is, who it talks to, and where it keeps its session state."""

    comp_id: str
    target_comp_id: str
    heartbeat_interval: int = 30
    store: Path | None = None


class ExchangeClient:
    """One FIX connection to an exchange."""

    def __init__(self, host: str, port: int, settings: ClientSettings) -> None:
        self._host = host
        self._port = port
        self._store: SessionStore = (
            FileStore(settings.store) if settings.store is not None else InMemoryStore()
        )
        self._session = FixSession(
            role=Role.INITIATOR,
            session_id=SessionId(
                sender_comp_id=settings.comp_id, target_comp_id=settings.target_comp_id
            ),
            heartbeat_interval=settings.heartbeat_interval,
            store=self._store,
        )
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._reports: asyncio.Queue[Fields] = asyncio.Queue()
        self._logged_on = asyncio.Event()
        self._closed = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []

    async def __aenter__(self) -> Self:
        await self.logon()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def logon(self, timeout: float = 5.0) -> None:
        """Connect and wait until the exchange has logged this session on."""
        self._reader, self._writer = await asyncio.open_connection(self._host, self._port)
        self._tasks = [asyncio.create_task(self._read()), asyncio.create_task(self._keep_time())]
        await self._act(self._session.connected(datetime.now(UTC)))
        await asyncio.wait_for(self._logged_on.wait(), timeout)

    async def send(self, fields: Fields) -> None:
        """Send an application message, such as a new order."""
        await self._act(self._session.send_application(fields, datetime.now(UTC)))

    async def report(self, timeout: float = 5.0) -> Fields:
        """The next message the exchange sent, waiting for it if necessary."""
        return await asyncio.wait_for(self._reports.get(), timeout)

    def pending_reports(self) -> int:
        """How many messages are waiting to be read."""
        return self._reports.qsize()

    async def close(self, reason: str = "done for the day") -> None:
        """Log out politely, then close the connection."""
        if self._writer is not None and not self._writer.is_closing():
            await self._act(self._session.logout(reason, datetime.now(UTC)))
        await self._shut_down()

    async def drop(self) -> None:
        """Close the connection without logging out, as a crashed client would."""
        await self._shut_down()

    async def _shut_down(self) -> None:
        for task in self._tasks:
            task.cancel()
        self._tasks = []
        if self._writer is not None and not self._writer.is_closing():
            self._writer.close()
        self._writer = None
        self._closed.set()
        if isinstance(self._store, FileStore):
            self._store.close()

    async def _read(self) -> None:
        assert self._reader is not None  # noqa: S101 - set in logon, before this task starts
        while True:
            data = await self._reader.read(READ_SIZE)
            if not data:
                self._closed.set()
                return
            await self._act(self._session.receive(data, datetime.now(UTC)))

    async def _keep_time(self) -> None:
        while True:
            await asyncio.sleep(TICK_SECONDS)
            await self._act(self._session.tick(datetime.now(UTC)))

    async def _act(self, actions: list[Action]) -> None:
        for action in actions:
            match action:
                case Send(data=payload):
                    if self._writer is not None and not self._writer.is_closing():
                        self._writer.write(payload)
                        await self._writer.drain()
                case Deliver(fields=fields):
                    await self._reports.put(fields)
                case LoggedOn():
                    self._logged_on.set()
                case LoggedOut(reason=reason):
                    logger.info("logged out: %s", reason)
                case Disconnect(reason=reason):
                    logger.info("disconnected: %s", reason)
                    self._closed.set()
