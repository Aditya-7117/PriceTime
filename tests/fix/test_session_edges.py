"""What the session does when things do not go to plan."""

import pytest

from pricetime.fix import tags
from pricetime.fix.tags import MsgType, SessionRejectReason
from pricetime.fix.wire import Fields, encode
from tests.fix.support import CLIENT, EXCHANGE, at, logged_on, messages, pair

REPORT: Fields = ((tags.MSG_TYPE, MsgType.EXECUTION_REPORT), (tags.EXEC_ID, "E1"))


def field(message: Fields, tag: int) -> str | None:
    return dict(message).get(tag)


def test_a_session_that_is_never_logged_on_to_is_dropped() -> None:
    _, server = pair()
    server.connected(at(0))

    server.tick(at(5))
    still_waiting = server.disconnected
    server.tick(at(20))

    assert still_waiting is None
    assert server.disconnected is not None
    assert "Logon" in server.disconnected


def test_nothing_is_sent_before_the_session_is_up() -> None:
    client, _ = pair()
    client.connected(at(0))

    client.send(REPORT, at(1))

    assert client.sent_types() == [MsgType.LOGON]


def test_an_application_message_without_its_type_is_a_programming_error() -> None:
    client, _server = logged_on()

    with pytest.raises(ValueError, match="MsgType"):
        client.send(((tags.EXEC_ID, "E1"),), at(1))


def test_a_message_without_a_sequence_number_is_rejected() -> None:
    _client, server = logged_on()

    server.receive(
        encode(
            (
                (tags.MSG_TYPE, MsgType.NEW_ORDER_SINGLE),
                (tags.SENDER_COMP_ID, CLIENT),
                (tags.TARGET_COMP_ID, EXCHANGE),
                (tags.SENDING_TIME, "20260921-09:15:00.000"),
            )
        ),
        at(1),
    )

    (reject,) = messages(server.pending)
    assert field(reject, tags.MSG_TYPE) == MsgType.REJECT
    assert field(reject, tags.SESSION_REJECT_REASON) == SessionRejectReason.REQUIRED_TAG_MISSING
    assert server.delivered == []


def test_a_logon_without_a_heartbeat_interval_is_refused() -> None:
    _, server = pair()
    server.connected(at(0))

    server.receive(
        encode(
            (
                (tags.MSG_TYPE, MsgType.LOGON),
                (tags.SENDER_COMP_ID, CLIENT),
                (tags.TARGET_COMP_ID, EXCHANGE),
                (tags.MSG_SEQ_NUM, "1"),
                (tags.SENDING_TIME, "20260921-09:15:00.000"),
                (tags.ENCRYPT_METHOD, "0"),
            )
        ),
        at(0),
    )

    assert server.disconnected is not None
    assert "heartbeat" in server.disconnected


def test_a_sequence_reset_that_goes_backwards_is_rejected() -> None:
    _client, server = logged_on()

    server.receive(_reset(seq=2, new_seq_no="1"), at(1))

    (reject,) = messages(server.pending)
    assert field(reject, tags.MSG_TYPE) == MsgType.REJECT
    assert field(reject, tags.SESSION_REJECT_REASON) == SessionRejectReason.VALUE_IS_INCORRECT


def test_a_gap_fill_that_goes_backwards_is_rejected() -> None:
    _client, server = logged_on()

    server.receive(_reset(seq=2, new_seq_no="1", gap_fill=True), at(1))

    (reject,) = messages(server.pending)
    assert field(reject, tags.SESSION_REJECT_REASON) == SessionRejectReason.VALUE_IS_INCORRECT


def test_a_gap_fill_carries_the_receiver_over_the_missing_messages() -> None:
    _client, server = logged_on()

    server.receive(_reset(seq=2, new_seq_no="5", gap_fill=True), at(1))
    server.receive(_order(seq=5), at(2))

    assert len(server.delivered) == 1
    assert server.disconnected is None


def test_a_reject_from_the_counterparty_is_noted_and_nothing_is_sent_back() -> None:
    _client, server = logged_on()

    server.receive(
        encode(
            (
                (tags.MSG_TYPE, MsgType.REJECT),
                (tags.SENDER_COMP_ID, CLIENT),
                (tags.TARGET_COMP_ID, EXCHANGE),
                (tags.MSG_SEQ_NUM, "2"),
                (tags.SENDING_TIME, "20260921-09:15:00.000"),
                (tags.REF_SEQ_NUM, "1"),
            )
        ),
        at(1),
    )

    assert server.pending == b""
    assert server.disconnected is None


def test_nothing_is_read_after_the_session_closes() -> None:
    _client, server = logged_on()
    server.receive(_order(seq=99), at(1))  # a gap: the server asks for a resend
    server.take()
    server.receive(_bad_company(seq=2), at(2))

    server.receive(_order(seq=2) + _order(seq=3), at(3))

    assert server.delivered == []


def test_a_second_logout_request_changes_nothing() -> None:
    client, _server = logged_on()
    client.logout("closing", at(1))
    first = client.take()

    client.logout("closing again", at(2))

    assert first != b""
    assert client.pending == b""


def _order(*, seq: int) -> bytes:
    return encode(
        (
            (tags.MSG_TYPE, MsgType.NEW_ORDER_SINGLE),
            (tags.SENDER_COMP_ID, CLIENT),
            (tags.TARGET_COMP_ID, EXCHANGE),
            (tags.MSG_SEQ_NUM, str(seq)),
            (tags.SENDING_TIME, "20260921-09:15:00.000"),
            (tags.CL_ORD_ID, f"order-{seq}"),
        )
    )


def _bad_company(*, seq: int) -> bytes:
    return encode(
        (
            (tags.MSG_TYPE, MsgType.NEW_ORDER_SINGLE),
            (tags.SENDER_COMP_ID, "SOMEONE ELSE"),
            (tags.TARGET_COMP_ID, EXCHANGE),
            (tags.MSG_SEQ_NUM, str(seq)),
            (tags.SENDING_TIME, "20260921-09:15:00.000"),
        )
    )


def _reset(*, seq: int, new_seq_no: str, gap_fill: bool = False) -> bytes:
    fields: Fields = (
        (tags.MSG_TYPE, MsgType.SEQUENCE_RESET),
        (tags.SENDER_COMP_ID, CLIENT),
        (tags.TARGET_COMP_ID, EXCHANGE),
        (tags.MSG_SEQ_NUM, str(seq)),
        (tags.SENDING_TIME, "20260921-09:15:00.000"),
        (tags.NEW_SEQ_NO, new_seq_no),
    )
    if gap_fill:
        fields = (*fields, (tags.GAP_FILL_FLAG, "Y"))
    return encode(fields)
