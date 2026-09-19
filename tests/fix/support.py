"""Two FIX sessions joined by a link the test controls.

The sessions never touch a socket, so a test can hand one side's bytes to the
other, or refuse to, and watch what each side does about it.
"""

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta

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
from pricetime.fix.store import InMemoryStore
from pricetime.fix.tags import MSG_TYPE
from pricetime.fix.wire import Decoder, Fields, Garbled

START = datetime(2026, 9, 21, 9, 15, tzinfo=UTC)
HEARTBEAT_SECONDS = 30

EXCHANGE = "PRICETIME"
CLIENT = "CLIENT1"


def at(seconds: float) -> datetime:
    """The session clock, `seconds` after the start of the test."""
    return START + timedelta(seconds=seconds)


class Peer:
    """One side of a link: its session, the bytes it wants to send, and what it received."""

    def __init__(self, session: FixSession) -> None:
        self.session = session
        self.pending = b""
        self.delivered: list[Fields] = []
        self.logged_on = False
        self.logged_out: str | None = None
        self.disconnected: str | None = None

    def apply(self, actions: Sequence[Action]) -> None:
        for action in actions:
            match action:
                case Send(data=data):
                    self.pending += data
                case Deliver(fields=fields):
                    self.delivered.append(fields)
                case LoggedOn():
                    self.logged_on = True
                case LoggedOut(reason=reason):
                    self.logged_out = reason
                case Disconnect(reason=reason):
                    self.disconnected = reason
                    self.logged_on = False

    def take(self) -> bytes:
        """Everything this side wants to send, cleared."""
        data, self.pending = self.pending, b""
        return data

    def connected(self, now: datetime) -> None:
        self.apply(self.session.connected(now))

    def receive(self, data: bytes, now: datetime) -> None:
        self.apply(self.session.receive(data, now))

    def tick(self, now: datetime) -> None:
        self.apply(self.session.tick(now))

    def send(self, fields: Fields, now: datetime) -> None:
        self.apply(self.session.send_application(fields, now))

    def logout(self, reason: str, now: datetime) -> None:
        self.apply(self.session.logout(reason, now))

    def sent_types(self) -> list[str]:
        """The message types in whatever this side has pending, for assertions."""
        return [dict(m)[MSG_TYPE] for m in messages(self.pending)]


def messages(data: bytes) -> list[Fields]:
    """Decode bytes captured from the link, ignoring anything garbled."""
    return [m for m in Decoder().feed(data) if not isinstance(m, Garbled)]


def pair(heartbeat: int = HEARTBEAT_SECONDS) -> tuple[Peer, Peer]:
    """An initiator and an acceptor that have not spoken yet."""
    initiator = FixSession(
        role=Role.INITIATOR,
        session_id=SessionId(sender_comp_id=CLIENT, target_comp_id=EXCHANGE),
        heartbeat_interval=heartbeat,
        store=InMemoryStore(),
    )
    acceptor = FixSession(
        role=Role.ACCEPTOR,
        session_id=SessionId(sender_comp_id=EXCHANGE, target_comp_id=CLIENT),
        heartbeat_interval=heartbeat,
        store=InMemoryStore(),
    )
    return Peer(initiator), Peer(acceptor)


def pump(client: Peer, server: Peer, now: datetime, *, rounds: int = 6) -> None:
    """Carry bytes back and forth until neither side has anything more to say."""
    for _ in range(rounds):
        outbound, inbound = client.take(), server.take()
        if not outbound and not inbound:
            return
        if outbound:
            server.receive(outbound, now)
        if inbound:
            client.receive(inbound, now)


def logged_on(heartbeat: int = HEARTBEAT_SECONDS, now: datetime = START) -> tuple[Peer, Peer]:
    """A pair that has completed the logon handshake."""
    client, server = pair(heartbeat)
    client.connected(now)
    server.connected(now)
    pump(client, server, now)
    return client, server
