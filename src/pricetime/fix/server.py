"""The exchange as a running program: FIX over TCP.

The sessions and the engines know nothing about sockets. This module is the part
that does. It accepts connections, hands each one's bytes to the right session,
and writes back whatever the session and the gateway produce.

Everything one batch of messages caused, the commands in the journals and the
reports the sessions are about to send, is forced to disk once before a single
acknowledgement leaves. That is group commit: one disk sync for a whole batch
rather than one for every order, and no client is ever told about an order the
disk does not hold.

A session outlives its connection. When a client drops, its sequence numbers and
everything sent to it stay on disk, so it can reconnect, log on and ask for
whatever it missed.
"""

import asyncio
import logging
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime

from pricetime.fix import tags
from pricetime.fix.config import ExchangeConfig
from pricetime.fix.gateway import Gateway, Outbound
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
from pricetime.fix.store import FileStore
from pricetime.fix.wire import Decoder, Garbled

logger = logging.getLogger(__name__)

READ_SIZE = 65536
TICK_SECONDS = 1.0

type _Outgoing = defaultdict[str, bytearray]


class ExchangeServer:
    """Listens for FIX connections and runs the exchange behind them."""

    def __init__(self, config: ExchangeConfig, *, host: str = "127.0.0.1", port: int = 0) -> None:
        self._config = config
        self._host = host
        self._requested_port = port
        self._gateway = Gateway(config)
        self._stores: dict[str, FileStore] = {}
        self._sessions: dict[str, FixSession] = {}
        self._writers: dict[str, asyncio.StreamWriter] = {}
        self._logged_out: set[str] = set()
        self._server: asyncio.Server | None = None
        self._timer: asyncio.Task[None] | None = None

    @property
    def port(self) -> int:
        """The port being listened on, which matters when the caller asked for any free one."""
        if self._server is None:
            raise RuntimeError("the server is not listening yet")
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        """Begin listening and start the clock that drives heartbeats."""
        self._server = await asyncio.start_server(
            self._connection, self._host, self._requested_port
        )
        self._timer = asyncio.create_task(self._keep_time())
        logger.info("exchange listening on %s:%d", self._host, self.port, extra={"port": self.port})

    async def close(self) -> None:
        """Stop listening, drop every connection and close every file."""
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        for writer in list(self._writers.values()):
            writer.close()
        self._writers.clear()
        for store in self._stores.values():
            store.close()
        self._gateway.close()

    async def _connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        comp_id: str | None = None
        try:
            while True:
                data = await reader.read(READ_SIZE)
                if not data:
                    break
                if comp_id is None:
                    comp_id = self._identify(data)
                    if comp_id is None:
                        logger.warning("connection from an unknown counterparty, dropping it")
                        break
                    self._writers[comp_id] = writer
                    self._session(comp_id).connected(datetime.now(UTC))
                await self._process(comp_id, data)
        finally:
            if comp_id is not None:
                self._dropped(comp_id)
            writer.close()

    def _identify(self, data: bytes) -> str | None:
        """Which counterparty is this? The first message says, or the connection is refused."""
        for message in Decoder().feed(data):
            if isinstance(message, Garbled):
                continue
            sender = dict(message).get(tags.SENDER_COMP_ID)
            return sender if sender in self._config.sessions else None
        return None

    def _session(self, comp_id: str) -> FixSession:
        """The session for a counterparty, with its store, created once and kept."""
        session = self._sessions.get(comp_id)
        if session is not None:
            return session
        settings = self._config.sessions[comp_id]
        store = FileStore(settings.store)
        self._stores[comp_id] = store
        session = FixSession(
            role=Role.ACCEPTOR,
            session_id=SessionId(
                sender_comp_id=self._config.comp_id, target_comp_id=settings.comp_id
            ),
            heartbeat_interval=self._config.heartbeat_interval,
            store=store,
        )
        self._sessions[comp_id] = session
        return session

    async def _process(self, comp_id: str, data: bytes) -> None:
        now = datetime.now(UTC)
        outgoing: _Outgoing = defaultdict(bytearray)
        self._act(comp_id, self._session(comp_id).receive(data, now), outgoing, now)
        self._commit()
        await self._write(outgoing)

    def _act(
        self, comp_id: str, actions: Sequence[Action], outgoing: _Outgoing, now: datetime
    ) -> None:
        """Carry out what a session asked for, gathering the bytes to send."""
        for action in actions:
            match action:
                case Send(data=payload):
                    outgoing[comp_id] += payload
                case Deliver(fields=fields):
                    for message in self._gateway.handle(comp_id, fields, now):
                        self._reply(message, outgoing, now)
                case LoggedOn():
                    logger.info("%s logged on", comp_id, extra={"session": comp_id})
                case LoggedOut(reason=reason):
                    logger.info("%s logged out: %s", comp_id, reason, extra={"session": comp_id})
                    self._logged_out.add(comp_id)
                case Disconnect(reason=reason):
                    logger.info("dropping %s: %s", comp_id, reason, extra={"session": comp_id})
                    self._hang_up(comp_id)

    def _reply(self, message: Outbound, outgoing: _Outgoing, now: datetime) -> None:
        """Send one gateway message through its own session, whoever it belongs to."""
        session = self._session(message.session)
        self._act(message.session, session.send_application(message.fields, now), outgoing, now)

    def _commit(self) -> None:
        """One sync for the whole batch, before anything is acknowledged."""
        if not self._config.force_to_disk:
            return
        self._gateway.commit()
        for store in self._stores.values():
            store.sync()

    async def _write(self, outgoing: _Outgoing) -> None:
        for comp_id, payload in outgoing.items():
            writer = self._writers.get(comp_id)
            if writer is None or writer.is_closing():
                continue
            writer.write(bytes(payload))
            await writer.drain()

    def _hang_up(self, comp_id: str) -> None:
        writer = self._writers.pop(comp_id, None)
        if writer is not None and not writer.is_closing():
            writer.close()

    def _dropped(self, comp_id: str) -> None:
        """A connection ended. The session stays; its orders may not.

        A client that said goodbye meant to go, so its orders are left where
        they are. A client that vanished did not, which is what cancel on
        disconnect is for.
        """
        now = datetime.now(UTC)
        session = self._sessions.get(comp_id)
        if session is not None:
            session.transport_lost()
        self._writers.pop(comp_id, None)
        if comp_id in self._logged_out:
            self._logged_out.discard(comp_id)
            return
        outgoing: _Outgoing = defaultdict(bytearray)
        for message in self._gateway.disconnected(comp_id, now):
            self._reply(message, outgoing, now)
        self._commit()

    async def _keep_time(self) -> None:
        """Give every session the time, so heartbeats go out and dead lines are noticed."""
        while True:
            await asyncio.sleep(TICK_SECONDS)
            now = datetime.now(UTC)
            outgoing: _Outgoing = defaultdict(bytearray)
            for comp_id, session in list(self._sessions.items()):
                self._act(comp_id, session.tick(now), outgoing, now)
            self._commit()
            await self._write(outgoing)
