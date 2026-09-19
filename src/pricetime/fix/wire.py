"""The FIX wire format: bytes in, checked fields out.

This is the only module that touches simplefix, which splits a byte stream into
tag and value pairs and builds messages. It does not check BodyLength or
CheckSum, so this module does. A message that fails either check is garbled: the
session discards it and changes nothing, exactly as the FIX session protocol
requires, because a corrupted message cannot be trusted to say what it appears
to say.

Fields arrive and leave without the framing (BeginString, BodyLength, CheckSum),
which only this module needs to see.
"""

from dataclasses import dataclass

import simplefix
from simplefix.errors import ParsingError

from pricetime.fix import tags

SOH = b"\x01"
BEGIN_STRING = "FIX.4.4"
CHECKSUM_FIELD_LENGTH = len(b"10=000\x01")

_FRAMING = frozenset({tags.BEGIN_STRING, tags.BODY_LENGTH, tags.CHECK_SUM})
_SHAPE = [tags.BEGIN_STRING, tags.BODY_LENGTH, tags.MSG_TYPE, tags.CHECK_SUM]

type Fields = tuple[tuple[int, str], ...]


@dataclass(frozen=True, slots=True)
class Garbled:
    """A message that failed its own integrity checks, with the bytes as received."""

    reason: str
    raw: bytes


def encode(fields: Fields) -> bytes:
    """Frame body fields into a complete FIX 4.4 message.

    The caller supplies the message type first, then the rest of the header and
    the body in the order they should appear. BeginString, BodyLength and
    CheckSum are added here.

    Raises:
        ValueError: If the first field is not MsgType, or the caller tries to
            write a framing field itself.
    """
    if not fields or fields[0][0] != tags.MSG_TYPE:
        raise ValueError("a FIX message starts with its MsgType (tag 35)")
    if any(tag in _FRAMING for tag, _ in fields[1:]):
        raise ValueError("BeginString, BodyLength and CheckSum are added by the encoder")
    message = simplefix.FixMessage()
    message.append_pair(tags.BEGIN_STRING, BEGIN_STRING, header=True)
    for tag, value in fields:
        message.append_pair(tag, value)
    return bytes(message.encode())


class Decoder:
    """Splits a stream of bytes into messages, checking each one before it is used."""

    def __init__(self) -> None:
        self._parser = simplefix.FixParser()

    def feed(self, data: bytes) -> list[Fields | Garbled]:
        """Decode whatever complete messages the bytes so far contain."""
        self._parser.append_buffer(data)
        decoded: list[Fields | Garbled] = []
        while True:
            try:
                message = self._parser.get_message()
            except ParsingError as error:
                self._parser.reset()
                decoded.append(Garbled(f"unreadable field: {error}", b""))
                continue
            if message is None:
                return decoded
            decoded.append(_checked(message))


def _checked(message: simplefix.FixMessage) -> Fields | Garbled:
    raw = bytes(message.encode(raw=True))
    try:
        pairs = tuple((int(tag), value.decode("ascii")) for tag, value in message.pairs)
    except (UnicodeDecodeError, ValueError):
        return Garbled("field values must be ASCII", raw)

    shape = [tag for tag, _ in pairs[:3]] + [pairs[-1][0]]
    if len(pairs) < len(_SHAPE) or shape != _SHAPE:
        return Garbled("a message runs BeginString, BodyLength, MsgType, then CheckSum", raw)
    if pairs[0][1] != BEGIN_STRING:
        return Garbled(f"not {BEGIN_STRING}, found {pairs[0][1]!r}", raw)

    body_start = len(f"8={BEGIN_STRING}\x019={pairs[1][1]}\x01")
    body_length = len(raw) - body_start - CHECKSUM_FIELD_LENGTH
    if pairs[1][1] != str(body_length):
        return Garbled(f"body length says {pairs[1][1]}, body is {body_length} bytes", raw)
    checksum = sum(raw[:-CHECKSUM_FIELD_LENGTH]) % 256
    if pairs[-1][1] != f"{checksum:03}":
        return Garbled(f"checksum says {pairs[-1][1]}, bytes give {checksum:03}", raw)
    return tuple(pair for pair in pairs if pair[0] not in _FRAMING)
