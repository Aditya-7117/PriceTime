"""The FIX 4.4 session layer: logon, heartbeats, sequence numbers, resend and gap fill.

Sans-IO. The session never opens a socket and never reads a clock: it takes
bytes and the current time, and returns actions for its caller to carry out.
That is what lets a test drop a message, duplicate it, or let an hour pass
between two lines, and watch exactly what the session does about it. The same
class is both ends of the conversation: an acceptor waits to be logged on to, an
initiator logs on.

The session layer's whole job is that both sides agree on what was said and in
what order. Every message carries a number, one higher than the last. A number
higher than expected means something was missed, so this side asks for a resend;
a number lower than expected, without the duplicate flag, means the conversation
cannot be trusted and the session ends.
"""

import enum
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from pricetime.fix import tags
from pricetime.fix.store import SessionStore, StoredMessage
from pricetime.fix.tags import MsgType, SessionRejectReason
from pricetime.fix.wire import Decoder, Fields, Garbled, encode

# A counterparty that has said nothing for this multiple of the agreed heartbeat
# interval is asked to prove it is there; twice over, it is treated as gone.
SILENCE_BEFORE_TEST_REQUEST = 1.2
SILENCE_BEFORE_DISCONNECT = 2.0
LOGON_TIMEOUT_SECONDS = 10


def fix_time(moment: datetime) -> str:
    """A UTC timestamp in the format FIX puts in SendingTime, to the millisecond."""
    return f"{moment:%Y%m%d-%H:%M:%S}.{moment.microsecond // 1000:03}"


class Role(enum.Enum):
    """Which end of the conversation this session is."""

    ACCEPTOR = "acceptor"
    INITIATOR = "initiator"


@dataclass(frozen=True, slots=True)
class SessionId:
    """Who this side is and who it expects to be talking to."""

    sender_comp_id: str
    target_comp_id: str


@dataclass(frozen=True, slots=True)
class Send:
    """Put these bytes on the wire."""

    data: bytes


@dataclass(frozen=True, slots=True)
class Deliver:
    """An application message that passed every session check."""

    fields: Fields


@dataclass(frozen=True, slots=True)
class LoggedOn:
    """The session is up and application messages may flow."""


@dataclass(frozen=True, slots=True)
class LoggedOut:
    """The counterparty asked to stop, in an orderly way."""

    reason: str


@dataclass(frozen=True, slots=True)
class Disconnect:
    """Close the connection now."""

    reason: str


type Action = Send | Deliver | LoggedOn | LoggedOut | Disconnect


class _State(enum.Enum):
    CONNECTED = "connected"
    ACTIVE = "active"
    LOGGING_OUT = "logging out"
    CLOSED = "closed"


class FixSession:
    """One FIX session, driven entirely by its caller."""

    def __init__(
        self,
        *,
        role: Role,
        session_id: SessionId,
        heartbeat_interval: int,
        store: SessionStore,
    ) -> None:
        self._role = role
        self._id = session_id
        self._heartbeat = timedelta(seconds=heartbeat_interval)
        self._heartbeat_interval = heartbeat_interval
        self._store = store
        self._decoder = Decoder()
        self._state = _State.CONNECTED
        self._opened = datetime.min
        self._last_sent = datetime.min
        self._last_received = datetime.min
        self._test_request_id: str | None = None
        self._awaiting_resend = False
        self._highest_seen = 0

    @property
    def session_id(self) -> SessionId:
        """Who this side is and who it is talking to."""
        return self._id

    @property
    def is_active(self) -> bool:
        """True once logged on, until the session closes."""
        return self._state is _State.ACTIVE

    def connected(self, now: datetime) -> list[Action]:
        """The transport is up. An initiator logs on; an acceptor waits to be logged on to."""
        self._opened = self._last_sent = self._last_received = now
        if self._role is Role.ACCEPTOR:
            return []
        logon: Fields = (
            (tags.ENCRYPT_METHOD, "0"),
            (tags.HEART_BT_INT, str(self._heartbeat_interval)),
        )
        return [self._transmit(MsgType.LOGON, logon, now)]

    def receive(self, data: bytes, now: datetime) -> list[Action]:
        """Take bytes off the wire and say what should happen."""
        actions: list[Action] = []
        for decoded in self._decoder.feed(data):
            if self._state is _State.CLOSED:
                break
            if isinstance(decoded, Garbled):
                # A garbled message is discarded without touching sequence
                # numbers. The gap it leaves is found when the next message
                # arrives, and a resend request repairs it.
                continue
            actions.extend(self._on_message(decoded, now))
        return actions

    def send_application(self, fields: Fields, now: datetime) -> list[Action]:
        """Send an application message, for example an execution report."""
        if self._state is not _State.ACTIVE:
            return []
        msg_type = dict(fields).get(tags.MSG_TYPE)
        if msg_type is None:
            raise ValueError("an application message needs its MsgType")
        body = tuple(pair for pair in fields if pair[0] != tags.MSG_TYPE)
        return [self._transmit(msg_type, body, now)]

    def tick(self, now: datetime) -> list[Action]:
        """Let time pass: send heartbeats, chase silence, give up on a dead session."""
        if self._state is _State.CONNECTED:
            if now - self._opened > timedelta(seconds=LOGON_TIMEOUT_SECONDS):
                return self._close("no Logon within the allowed time")
            return []
        if self._state is not _State.ACTIVE:
            return []

        actions: list[Action] = []
        silence = now - self._last_received
        if self._test_request_id is not None:
            if silence >= self._heartbeat * SILENCE_BEFORE_DISCONNECT:
                return self._close("no answer to our test request")
        elif silence >= self._heartbeat * SILENCE_BEFORE_TEST_REQUEST:
            self._test_request_id = fix_time(now)
            actions.append(
                self._transmit(
                    MsgType.TEST_REQUEST, ((tags.TEST_REQ_ID, self._test_request_id),), now
                )
            )
        if now - self._last_sent >= self._heartbeat:
            actions.append(self._transmit(MsgType.HEARTBEAT, (), now))
        return actions

    def logout(self, reason: str, now: datetime) -> list[Action]:
        """Ask the counterparty to stop, and wait for it to agree."""
        if self._state not in {_State.ACTIVE, _State.CONNECTED}:
            return []
        self._state = _State.LOGGING_OUT
        return [self._transmit(MsgType.LOGOUT, ((tags.TEXT, reason),), now)]

    def _on_message(self, fields: Fields, now: datetime) -> list[Action]:
        header = dict(fields)
        if (refusal := self._refuse_bad_header(header, now)) is not None:
            return refusal
        self._last_received = now
        msg_type = header[tags.MSG_TYPE]
        if self._state is _State.CONNECTED and msg_type != MsgType.LOGON:
            return self._close("the first message on a session must be a Logon")
        if msg_type == MsgType.LOGON and header.get(tags.RESET_SEQ_NUM_FLAG) == "Y":
            self._store.reset()
        if msg_type == MsgType.SEQUENCE_RESET and header.get(tags.GAP_FILL_FLAG) != "Y":
            # A reset that is not a gap fill applies whatever its own number says.
            return self._sequence_reset(header, now)
        if (out_of_order := self._on_out_of_order(msg_type, header, now)) is not None:
            return out_of_order

        self._store.record_inbound(int(header[tags.MSG_SEQ_NUM]))
        self._awaiting_resend = False
        return self._apply(msg_type, header, fields, now)

    def _refuse_bad_header(self, header: dict[int, str], now: datetime) -> list[Action] | None:
        """Refuse a message whose header cannot be trusted, or None to carry on."""
        if header.get(tags.MSG_TYPE) is None or not header.get(tags.MSG_SEQ_NUM, "").isdigit():
            return self._reject(
                header, SessionRejectReason.REQUIRED_TAG_MISSING, "MsgType and MsgSeqNum", now
            )
        if (
            header.get(tags.SENDER_COMP_ID) != self._id.target_comp_id
            or header.get(tags.TARGET_COMP_ID) != self._id.sender_comp_id
        ):
            refusal = self._reject(header, SessionRejectReason.COMP_ID_PROBLEM, "CompID", now)
            refusal.extend(self.logout("CompID mismatch", now))
            refusal.extend(self._close("CompID mismatch"))
            return refusal
        return None

    def _on_out_of_order(
        self, msg_type: str, header: dict[int, str], now: datetime
    ) -> list[Action] | None:
        """Deal with a sequence number that is not the one expected, or None if it is."""
        sequence = int(header[tags.MSG_SEQ_NUM])
        expected = self._store.next_inbound
        if sequence == expected:
            return None
        if sequence < expected:
            if header.get(tags.POSS_DUP_FLAG) == "Y":
                return []  # already seen and already acted on
            problem = f"MsgSeqNum too low, expected {expected} but received {sequence}"
            refusal = self.logout(problem, now)
            refusal.extend(self._close(problem))
            return refusal
        actions: list[Action] = []
        if msg_type == MsgType.RESEND_REQUEST:
            # Answer first: the counterparty may be recovering from the same
            # gap, and two sides waiting on each other never recover.
            actions.extend(self._resend(header, now))
        actions.extend(self._ask_for_resend(expected, sequence, now))
        return actions

    def _apply(
        self, msg_type: str, header: dict[int, str], fields: Fields, now: datetime
    ) -> list[Action]:
        handler = _ADMIN_HANDLERS.get(msg_type)
        if handler is not None:
            return handler(self, header, now)
        if self._state is not _State.ACTIVE:
            return self._close("application message before logon")
        return [Deliver(fields)]

    def _on_heartbeat(self, _header: dict[int, str], _now: datetime) -> list[Action]:
        """The counterparty is alive, which is all a heartbeat says."""
        self._test_request_id = None
        return []

    def _on_test_request(self, header: dict[int, str], now: datetime) -> list[Action]:
        answer: Fields = ((tags.TEST_REQ_ID, header.get(tags.TEST_REQ_ID, "")),)
        return [self._transmit(MsgType.HEARTBEAT, answer, now)]

    def _on_reject(self, _header: dict[int, str], _now: datetime) -> list[Action]:
        """The counterparty refused something of ours. It says so; there is nothing to send back."""
        return []

    def _on_logon(self, header: dict[int, str], now: datetime) -> list[Action]:
        raw_interval = header.get(tags.HEART_BT_INT, "")
        if not raw_interval.isdigit():
            return self._close("Logon without a heartbeat interval")
        self._heartbeat_interval = int(raw_interval)
        self._heartbeat = timedelta(seconds=self._heartbeat_interval)
        self._state = _State.ACTIVE
        if self._role is Role.INITIATOR:
            return [LoggedOn()]
        answer: Fields = (
            (tags.ENCRYPT_METHOD, "0"),
            (tags.HEART_BT_INT, raw_interval),
        )
        return [self._transmit(MsgType.LOGON, answer, now), LoggedOn()]

    def _on_logout(self, header: dict[int, str], now: datetime) -> list[Action]:
        reason = header.get(tags.TEXT, "")
        actions: list[Action] = [LoggedOut(reason)]
        if self._state is not _State.LOGGING_OUT:
            actions.append(self._transmit(MsgType.LOGOUT, ((tags.TEXT, reason),), now))
        actions.extend(self._close(f"logged out: {reason}" if reason else "logged out"))
        return actions

    def _sequence_reset(self, header: dict[int, str], now: datetime) -> list[Action]:
        """A reset without gap fill sets the expected number, whatever its own number says."""
        raw = header.get(tags.NEW_SEQ_NO, "")
        if not raw.isdigit() or int(raw) < self._store.next_inbound:
            return self._reject(header, SessionRejectReason.VALUE_IS_INCORRECT, "NewSeqNo", now)
        self._store.expect_inbound(int(raw))
        self._awaiting_resend = False
        return []

    def _gap_fill(self, header: dict[int, str], now: datetime) -> list[Action]:
        """A gap fill skips over messages the counterparty will not repeat."""
        raw = header.get(tags.NEW_SEQ_NO, "")
        if not raw.isdigit() or int(raw) < self._store.next_inbound:
            return self._reject(header, SessionRejectReason.VALUE_IS_INCORRECT, "NewSeqNo", now)
        self._store.expect_inbound(int(raw))
        return []

    def _resend(self, header: dict[int, str], now: datetime) -> list[Action]:
        """Repeat what was sent in the requested range, gap filling anything not worth repeating."""
        begin = int(header.get(tags.BEGIN_SEQ_NO, "1") or "1")
        raw_end = int(header.get(tags.END_SEQ_NO, "0") or "0")
        last = self._store.next_outbound - 1
        end = last if raw_end == 0 else min(raw_end, last)

        actions: list[Action] = []
        skipped: StoredMessage | None = None
        for message in self._store.between(begin, end):
            if message.admin:
                # Heartbeats and the like are meaningless later; they are skipped
                # over with one sequence reset instead.
                skipped = skipped or message
                continue
            if skipped is not None:
                actions.append(self._gap_fill_to(skipped, message.sequence, now))
                skipped = None
            actions.append(self._retransmit(message, now))
        if skipped is not None:
            actions.append(self._gap_fill_to(skipped, self._store.next_outbound, now))
        return actions

    def _ask_for_resend(self, expected: int, seen: int, now: datetime) -> list[Action]:
        """Ask for everything from `expected` onwards, without asking twice for nothing.

        A resend can itself be lost, so one request is not enough: the session
        asks again whenever the counterparty sends something new that still
        cannot be used. Repeats of a message already seen change nothing, which
        keeps a stalled session from turning into a storm of requests.
        """
        if self._awaiting_resend and seen <= self._highest_seen:
            return []
        self._highest_seen = max(seen, self._highest_seen)
        self._awaiting_resend = True
        request: Fields = ((tags.BEGIN_SEQ_NO, str(expected)), (tags.END_SEQ_NO, "0"))
        return [self._transmit(MsgType.RESEND_REQUEST, request, now)]

    def _reject(
        self,
        header: dict[int, str],
        reason: SessionRejectReason,
        text: str,
        now: datetime,
    ) -> list[Action]:
        body: Fields = (
            (tags.REF_SEQ_NUM, header.get(tags.MSG_SEQ_NUM, "0")),
            (tags.REF_MSG_TYPE, header.get(tags.MSG_TYPE, "")),
            (tags.SESSION_REJECT_REASON, reason),
            (tags.TEXT, text),
        )
        return [self._transmit(MsgType.REJECT, body, now)]

    def _close(self, reason: str) -> list[Action]:
        self._state = _State.CLOSED
        return [Disconnect(reason)]

    def _transmit(self, msg_type: str, body: Fields, now: datetime) -> Send:
        sequence = self._store.next_outbound
        sending_time = fix_time(now)
        self._store.record_outbound(
            StoredMessage(
                sequence=sequence,
                msg_type=msg_type,
                body=body,
                sending_time=sending_time,
                admin=msg_type in tags.ADMIN_MESSAGES,
            )
        )
        self._last_sent = now
        return Send(encode((*self._header(msg_type, sequence, sending_time), *body)))

    def _retransmit(self, message: StoredMessage, now: datetime) -> Send:
        """Send a stored message again, under its original number."""
        header = self._header(
            message.msg_type,
            message.sequence,
            fix_time(now),
            original_time=message.sending_time,
        )
        self._last_sent = now
        return Send(encode((*header, *message.body)))

    def _gap_fill_to(self, first_skipped: StoredMessage, new_sequence: int, now: datetime) -> Send:
        body: Fields = ((tags.GAP_FILL_FLAG, "Y"), (tags.NEW_SEQ_NO, str(new_sequence)))
        header = self._header(
            MsgType.SEQUENCE_RESET,
            first_skipped.sequence,
            fix_time(now),
            original_time=first_skipped.sending_time,
        )
        self._last_sent = now
        return Send(encode((*header, *body)))

    def _header(
        self,
        msg_type: str,
        sequence: int,
        sending_time: str,
        *,
        original_time: str | None = None,
    ) -> Fields:
        header: list[tuple[int, str]] = [
            (tags.MSG_TYPE, msg_type),
            (tags.SENDER_COMP_ID, self._id.sender_comp_id),
            (tags.TARGET_COMP_ID, self._id.target_comp_id),
            (tags.MSG_SEQ_NUM, str(sequence)),
        ]
        if original_time is not None:
            header.append((tags.POSS_DUP_FLAG, "Y"))
            header.append((tags.ORIG_SENDING_TIME, original_time))
        header.append((tags.SENDING_TIME, sending_time))
        return tuple(header)


# Built after the class so the handlers can be named: which admin message goes where.
_ADMIN_HANDLERS: dict[str, Callable[[FixSession, dict[int, str], datetime], list[Action]]] = {
    MsgType.LOGON: FixSession._on_logon,
    MsgType.HEARTBEAT: FixSession._on_heartbeat,
    MsgType.TEST_REQUEST: FixSession._on_test_request,
    MsgType.RESEND_REQUEST: FixSession._resend,
    MsgType.SEQUENCE_RESET: FixSession._gap_fill,
    MsgType.REJECT: FixSession._on_reject,
    MsgType.LOGOUT: FixSession._on_logout,
}
