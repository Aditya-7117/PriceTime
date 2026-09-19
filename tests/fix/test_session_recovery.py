"""Recovery: whatever the connection loses, both sides end up agreeing.

TCP does not reorder or corrupt a stream, but a connection that drops takes
whatever was in flight with it. That is the fault this models, and the resend
mechanism is what repairs it. A restart is the same problem over a longer gap,
which is why the exchange keeps its session state on disk.
"""

from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st

from pricetime.fix import tags
from pricetime.fix.session import FixSession, Role, SessionId
from pricetime.fix.store import FileStore
from pricetime.fix.tags import MsgType
from pricetime.fix.wire import Fields, encode
from tests.fix.support import CLIENT, EXCHANGE, Peer, at, logged_on, messages, pump

REPORT: Fields = ((tags.MSG_TYPE, MsgType.EXECUTION_REPORT), (tags.EXEC_ID, "E1"))


def order(client_order_id: str) -> Fields:
    return ((tags.MSG_TYPE, MsgType.NEW_ORDER_SINGLE), (tags.CL_ORD_ID, client_order_id))


def client_order_ids(peer: Peer) -> list[str]:
    return [dict(message)[tags.CL_ORD_ID] for message in peer.delivered]


@given(st.lists(st.booleans(), max_size=25))
def test_every_order_arrives_once_and_in_order_whatever_the_connection_loses(
    losses: list[bool],
) -> None:
    client, server = logged_on()

    for index, lost in enumerate(losses):
        client.send(order(f"order-{index}"), at(index + 1))
        in_flight = client.take()
        if lost:
            continue
        server.receive(in_flight, at(index + 1))
        client.receive(server.take(), at(index + 1))

    # One last order that always arrives, so any gap is noticed rather than
    # waiting on a heartbeat, then a quiet line until both sides have finished.
    client.send(order("last"), at(100))
    for extra in range(12):
        pump(client, server, at(100 + extra))

    expected = [f"order-{index}" for index in range(len(losses))] + ["last"]
    assert client_order_ids(server) == expected
    assert server.disconnected is None


def test_a_restarted_exchange_carries_on_from_the_numbers_it_stored(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    with _exchange(path) as exchange:
        exchange.receive(_from_client(MsgType.LOGON, seq=1), at(0))
        exchange.apply(exchange.session.send_application(REPORT, at(1)))
        exchange.take()

    with _exchange(path) as restarted:
        restarted.receive(_from_client(MsgType.LOGON, seq=2), at(10))

        (logon,) = messages(restarted.pending)
        assert dict(logon)[tags.MSG_TYPE] == MsgType.LOGON
        assert dict(logon)[tags.MSG_SEQ_NUM] == "3"


def test_a_restarted_exchange_can_still_resend_what_it_sent_before(tmp_path: Path) -> None:
    path = tmp_path / "session.jsonl"
    with _exchange(path) as exchange:
        exchange.receive(_from_client(MsgType.LOGON, seq=1), at(0))
        exchange.apply(exchange.session.send_application(REPORT, at(1)))
        exchange.take()  # the report never reaches the client

    with _exchange(path) as restarted:
        restarted.receive(_from_client(MsgType.LOGON, seq=2), at(10))
        restarted.take()

        restarted.receive(
            _from_client(
                MsgType.RESEND_REQUEST,
                seq=3,
                extra=((tags.BEGIN_SEQ_NO, "2"), (tags.END_SEQ_NO, "0")),
            ),
            at(11),
        )

        resent = messages(restarted.pending)
        assert [dict(m)[tags.MSG_TYPE] for m in resent] == [
            MsgType.EXECUTION_REPORT,
            MsgType.SEQUENCE_RESET,  # the logon it sent after the restart is not worth repeating
        ]
        assert dict(resent[0])[tags.EXEC_ID] == "E1"
        assert dict(resent[0])[tags.MSG_SEQ_NUM] == "2"
        assert dict(resent[0])[tags.POSS_DUP_FLAG] == "Y"
        assert dict(resent[1])[tags.NEW_SEQ_NO] == "4"


@contextmanager
def _exchange(path: Path) -> Generator[Peer]:
    """An acceptor whose session state lives on disk, closed when the test is done."""
    store = FileStore(path)
    try:
        session = FixSession(
            role=Role.ACCEPTOR,
            session_id=SessionId(sender_comp_id=EXCHANGE, target_comp_id=CLIENT),
            heartbeat_interval=30,
            store=store,
        )
        peer = Peer(session)
        peer.connected(at(0))
        yield peer
    finally:
        store.close()


def _from_client(msg_type: str, *, seq: int, extra: Fields = ()) -> bytes:
    fields: Fields = (
        (tags.MSG_TYPE, msg_type),
        (tags.SENDER_COMP_ID, CLIENT),
        (tags.TARGET_COMP_ID, EXCHANGE),
        (tags.MSG_SEQ_NUM, str(seq)),
        (tags.SENDING_TIME, "20260921-09:15:00.000"),
        *extra,
    )
    if msg_type == MsgType.LOGON:
        fields = (*fields, (tags.ENCRYPT_METHOD, "0"), (tags.HEART_BT_INT, "30"))
    return encode(fields)


def test_a_resend_that_is_itself_lost_is_asked_for_again() -> None:
    client, server = logged_on()
    client.send(order("first"), at(1))
    client.take()  # lost

    client.send(order("second"), at(2))
    server.receive(client.take(), at(2))  # server notices the gap and asks
    client.receive(server.take(), at(2))
    client.take()  # the resend is lost too
    client.send(order("third"), at(3))
    server.receive(client.take(), at(3))

    assert server.sent_types() == [MsgType.RESEND_REQUEST]
