import pytest

from pricetime.fix.wire import Decoder, Fields, Garbled, encode

# Derived by hand: the body is 58 bytes and the checksum of everything before
# the checksum field is 014.
HEARTBEAT = (
    b"8=FIX.4.4\x019=58\x0135=0\x0149=EXCHANGE\x0156=CLIENT1\x0134=7\x01"
    b"52=20260919-09:15:00.000\x0110=014\x01"
)
FIELDS: Fields = (
    (35, "0"),
    (49, "EXCHANGE"),
    (56, "CLIENT1"),
    (34, "7"),
    (52, "20260919-09:15:00.000"),
)


def test_encoding_adds_the_begin_string_body_length_and_checksum() -> None:
    assert encode(FIELDS) == HEARTBEAT


def test_a_message_must_start_with_its_message_type() -> None:
    with pytest.raises(ValueError, match="MsgType"):
        encode(((49, "EXCHANGE"), (35, "0")))


def test_decoding_returns_the_fields_inside_the_framing() -> None:
    assert Decoder().feed(HEARTBEAT) == [FIELDS]


def test_a_message_split_across_reads_is_decoded_once_complete() -> None:
    decoder = Decoder()

    first = decoder.feed(HEARTBEAT[:20])
    second = decoder.feed(HEARTBEAT[20:40])
    third = decoder.feed(HEARTBEAT[40:])

    assert first == []
    assert second == []
    assert third == [FIELDS]


def test_two_messages_in_one_read_are_decoded_in_order() -> None:
    second = encode(((35, "1"), (49, "EXCHANGE"), (56, "CLIENT1"), (34, "8"), (112, "TEST")))

    decoded = Decoder().feed(HEARTBEAT + second)

    assert decoded == [
        FIELDS,
        ((35, "1"), (49, "EXCHANGE"), (56, "CLIENT1"), (34, "8"), (112, "TEST")),
    ]


def test_a_wrong_checksum_is_garbled() -> None:
    corrupted = HEARTBEAT.replace(b"10=014", b"10=099")

    (decoded,) = Decoder().feed(corrupted)

    assert isinstance(decoded, Garbled)
    assert "checksum" in decoded.reason


def test_a_wrong_body_length_is_garbled() -> None:
    corrupted = encode(FIELDS).replace(b"9=58", b"9=57")

    (decoded,) = Decoder().feed(corrupted)

    assert isinstance(decoded, Garbled)
    assert "body length" in decoded.reason


def test_another_fix_version_is_garbled() -> None:
    other = HEARTBEAT.replace(b"8=FIX.4.4", b"8=FIX.4.2")

    (decoded,) = Decoder().feed(other)

    assert isinstance(decoded, Garbled)
    assert "FIX.4.4" in decoded.reason


def test_a_field_that_is_not_a_number_is_garbled() -> None:
    (decoded,) = Decoder().feed(b"8=FIX.4.4\x019=12\x01AB=0\x0110=000\x01")

    assert isinstance(decoded, Garbled)


def test_decoding_carries_on_after_a_garbled_message() -> None:
    corrupted = HEARTBEAT.replace(b"10=014", b"10=099")

    decoded = Decoder().feed(corrupted + HEARTBEAT)

    assert isinstance(decoded[0], Garbled)
    assert decoded[1] == FIELDS
