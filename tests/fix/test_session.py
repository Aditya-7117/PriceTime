"""The FIX session: logon, heartbeats, sequence numbers, resend and gap fill."""

from pricetime.fix import tags
from pricetime.fix.tags import MsgType
from pricetime.fix.wire import Fields, encode
from tests.fix.support import CLIENT, EXCHANGE, at, logged_on, messages, pair, pump

ORDER: Fields = ((tags.MSG_TYPE, MsgType.NEW_ORDER_SINGLE), (tags.CL_ORD_ID, "A1"))


def field(message: Fields, tag: int) -> str | None:
    return dict(message).get(tag)


def test_logon_handshake_brings_both_sides_up() -> None:
    client, server = pair()

    client.connected(at(0))
    server.connected(at(0))
    pump(client, server, at(0))

    assert client.logged_on
    assert server.logged_on
    assert client.disconnected is None


def test_the_first_message_must_be_a_logon() -> None:
    _, server = pair()
    server.connected(at(0))

    server.receive(_raw(MsgType.NEW_ORDER_SINGLE, seq=1), at(0))

    assert server.disconnected is not None
    assert "Logon" in server.disconnected


def test_a_logon_from_the_wrong_company_is_refused() -> None:
    _, server = pair()
    server.connected(at(0))

    server.receive(_raw(MsgType.LOGON, seq=1, sender="IMPOSTOR"), at(0))

    assert server.sent_types() == [MsgType.REJECT, MsgType.LOGOUT]
    assert server.disconnected is not None


def test_application_messages_are_delivered_with_rising_sequence_numbers() -> None:
    client, server = logged_on()

    client.send(ORDER, at(1))
    client.send(ORDER, at(2))
    pump(client, server, at(2))

    assert len(server.delivered) == 2
    assert [field(m, tags.MSG_SEQ_NUM) for m in server.delivered] == ["2", "3"]
    assert [field(m, tags.CL_ORD_ID) for m in server.delivered] == ["A1", "A1"]


def test_a_heartbeat_goes_out_after_the_agreed_silence() -> None:
    client, _server = logged_on(heartbeat=30)

    client.tick(at(29))
    quiet = client.take()
    client.tick(at(31))

    assert quiet == b""
    assert client.sent_types() == [MsgType.HEARTBEAT]


def test_silence_brings_a_test_request_and_then_a_disconnect() -> None:
    client, _server = logged_on(heartbeat=10)

    client.tick(at(13))
    types = client.sent_types()
    client.take()
    client.tick(at(40))

    assert types == [MsgType.TEST_REQUEST]
    assert client.disconnected is not None
    assert "test request" in client.disconnected


def test_a_test_request_is_answered_with_a_heartbeat_carrying_its_id() -> None:
    client, server = logged_on(heartbeat=10)
    client.tick(at(13))

    server.receive(client.take(), at(13))

    (heartbeat,) = messages(server.pending)
    assert field(heartbeat, tags.MSG_TYPE) == MsgType.HEARTBEAT
    assert field(heartbeat, tags.TEST_REQ_ID) is not None


def test_answering_a_test_request_keeps_the_session_alive() -> None:
    client, server = logged_on(heartbeat=10)
    client.tick(at(13))

    pump(client, server, at(13))
    client.tick(at(40))

    assert client.disconnected is None


def test_a_message_marked_as_a_duplicate_is_ignored() -> None:
    client, server = logged_on()
    client.send(ORDER, at(1))
    first = client.take()
    server.receive(first, at(1))

    server.receive(_mark_duplicate(first), at(2))

    assert len(server.delivered) == 1


def test_a_sequence_number_that_is_too_low_ends_the_session() -> None:
    client, server = logged_on()
    client.send(ORDER, at(1))
    server.receive(client.take(), at(1))

    server.receive(_raw(MsgType.NEW_ORDER_SINGLE, seq=2), at(2))

    assert MsgType.LOGOUT in server.sent_types()
    assert server.disconnected is not None
    assert "too low" in server.disconnected


def test_a_gap_makes_the_receiver_ask_for_a_resend() -> None:
    client, server = logged_on()
    client.send(ORDER, at(1))
    client.take()  # the first order never arrives
    client.send(ORDER, at(2))

    server.receive(client.take(), at(2))

    assert server.sent_types() == [MsgType.RESEND_REQUEST]
    (request,) = messages(server.pending)
    assert field(request, tags.BEGIN_SEQ_NO) == "2"
    assert field(request, tags.END_SEQ_NO) == "0"
    assert server.delivered == []


def test_the_resend_repeats_orders_and_gap_fills_everything_else() -> None:
    client, server = logged_on(heartbeat=10)
    client.send(ORDER, at(1))
    client.take()  # lost
    client.tick(at(12))  # a heartbeat, also lost
    client.take()
    client.send(ORDER, at(20))
    server.receive(client.take(), at(20))

    client.receive(server.take(), at(20))

    resent = messages(client.pending)
    assert [field(m, tags.MSG_TYPE) for m in resent] == [
        MsgType.NEW_ORDER_SINGLE,
        MsgType.SEQUENCE_RESET,
        MsgType.NEW_ORDER_SINGLE,
    ]
    assert field(resent[0], tags.POSS_DUP_FLAG) == "Y"
    assert field(resent[0], tags.ORIG_SENDING_TIME) is not None
    assert field(resent[1], tags.GAP_FILL_FLAG) == "Y"
    assert field(resent[1], tags.NEW_SEQ_NO) == "4"


def test_orders_lost_in_a_gap_arrive_once_the_resend_completes() -> None:
    client, server = logged_on()
    client.send(((tags.MSG_TYPE, MsgType.NEW_ORDER_SINGLE), (tags.CL_ORD_ID, "first")), at(1))
    client.take()  # lost
    client.send(((tags.MSG_TYPE, MsgType.NEW_ORDER_SINGLE), (tags.CL_ORD_ID, "second")), at(2))

    pump(client, server, at(3))

    assert [field(m, tags.CL_ORD_ID) for m in server.delivered] == ["first", "second"]


def test_a_sequence_reset_moves_the_expected_number_forward() -> None:
    _client, server = logged_on()

    server.receive(_raw(MsgType.SEQUENCE_RESET, seq=2, extra=((tags.NEW_SEQ_NO, "9"),)), at(1))
    server.receive(_raw(MsgType.NEW_ORDER_SINGLE, seq=9), at(2))

    assert len(server.delivered) == 1
    assert server.disconnected is None


def test_a_garbled_message_changes_nothing() -> None:
    client, server = logged_on()
    client.send(ORDER, at(1))
    good = client.take()

    server.receive(good.replace(b"10=", b"1x="), at(1))
    server.receive(good, at(2))

    assert len(server.delivered) == 1
    assert server.disconnected is None


def test_logout_is_answered_and_both_sides_close() -> None:
    client, server = logged_on()

    client.logout("done for the day", at(1))
    pump(client, server, at(1))

    assert server.logged_out == "done for the day"
    assert server.disconnected is not None
    assert client.disconnected is not None


def _raw(
    msg_type: str,
    *,
    seq: int,
    sender: str = CLIENT,
    extra: Fields = (),
) -> bytes:
    """A message built outside the session, to feed it something it did not expect."""
    fields: Fields = (
        (tags.MSG_TYPE, msg_type),
        (tags.SENDER_COMP_ID, sender),
        (tags.TARGET_COMP_ID, EXCHANGE),
        (tags.MSG_SEQ_NUM, str(seq)),
        (tags.SENDING_TIME, "20260921-09:15:00.000"),
        *extra,
    )
    if msg_type == MsgType.LOGON:
        fields = (*fields, (tags.ENCRYPT_METHOD, "0"), (tags.HEART_BT_INT, "30"))
    return encode(fields)


def _mark_duplicate(raw: bytes) -> bytes:
    (message,) = messages(raw)
    fields = tuple((tag, value) for tag, value in message if tag not in {tags.POSS_DUP_FLAG})
    return encode((*fields, (tags.POSS_DUP_FLAG, "Y")))
