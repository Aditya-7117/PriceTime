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


def framed(body: str) -> bytes:
    """A message framed by hand, so a test can build one the encoder would refuse."""
    head = f"8=FIX.4.4\x019={len(body)}\x01"
    checksum = sum((head + body).encode()) % 256
    return f"{head}{body}10={checksum:03}\x01".encode()


def test_framing_fields_are_not_the_callers_to_write() -> None:
    with pytest.raises(ValueError, match="BodyLength"):
        encode(((35, "0"), (9, "58")))


def test_a_value_that_is_not_ascii_is_garbled() -> None:
    (decoded,) = Decoder().feed(encode(((35, "0"), (58, "café"))))

    assert isinstance(decoded, Garbled)
    assert "ASCII" in decoded.reason


def test_a_message_whose_type_is_not_third_is_garbled() -> None:
    (decoded,) = Decoder().feed(framed("49=EXCHANGE\x0135=0\x01"))

    assert isinstance(decoded, Garbled)
    assert "MsgType" in decoded.reason


def test_a_damaged_message_with_nothing_after_it_leaves_the_decoder_empty() -> None:
    decoder = Decoder()

    decoded = decoder.feed(HEARTBEAT.replace(b"9=58", b"9=57"))

    assert [type(d) for d in decoded] == [Garbled]
    assert decoder.feed(HEARTBEAT) == [FIELDS]


def test_a_message_with_broken_framing_does_not_swallow_the_next_one() -> None:
    # The checksum field's tag is damaged, so the framing runs on into the next
    # message. The decoder must go back to the following BeginString.
    broken = HEARTBEAT.replace(b"10=014", b"1x=014")

    decoded = Decoder().feed(broken + HEARTBEAT)

    assert isinstance(decoded[0], Garbled)
    assert decoded[1:] == [FIELDS]
