"""The journal's line format: one JSON object per line, decoded strictly.

The first line of a journal is a header naming the format, its version and the
market rules the engine ran under. Every later line is one command with its
sequence number. Decoding checks every field and its type, and refuses anything
unexpected rather than guessing, because replay is the source of truth.
"""

import json
from collections.abc import Callable
from typing import assert_never

from pricetime.commands import (
    CancelOrder,
    Command,
    ModifyOrder,
    NewLimitOrder,
    NewMarketOrder,
    Validity,
)
from pricetime.orders import Side
from pricetime.rules import MarketRules, PriceBand

FORMAT = "pricetime-journal"
VERSION = 1

type _Record = dict[str, object]


class DecodeError(ValueError):
    """A line does not decode to a valid header or command."""


def encode_header(rules: MarketRules) -> str:
    """The first line of a journal written under `rules`."""
    band = rules.price_band
    encoded_rules = {
        "price_band": None if band is None else {"lower": band.lower, "upper": band.upper},
    }
    return _dumps({"format": FORMAT, "version": VERSION, "rules": encoded_rules})


def decode_header(line: str) -> MarketRules:
    """The market rules named in a journal's first line.

    Raises:
        DecodeError: If the line is not a header for this format and version.
    """
    try:
        header = json.loads(line)
    except json.JSONDecodeError as error:
        raise DecodeError("not a pricetime journal") from error
    if not isinstance(header, dict) or header.get("format") != FORMAT:
        raise DecodeError("not a pricetime journal")
    if header.get("version") != VERSION:
        raise DecodeError(f"unsupported version {header.get('version')!r}")
    if set(header) != {"format", "version", "rules"}:
        raise DecodeError(f"market rules missing, header fields are {sorted(header)}")
    return _decode_rules(header["rules"])


def encode_command(sequence: int, command: Command) -> str:
    """One journal line for a command and its sequence number."""
    record: _Record
    match command:
        case NewLimitOrder():
            record = {
                "seq": sequence,
                "type": "limit",
                "side": command.side.value,
                "price": command.price,
                "quantity": command.quantity,
                "validity": command.validity.value,
            }
        case NewMarketOrder():
            record = {
                "seq": sequence,
                "type": "market",
                "side": command.side.value,
                "quantity": command.quantity,
            }
        case CancelOrder():
            record = {"seq": sequence, "type": "cancel", "order_id": command.order_id}
        case ModifyOrder():
            record = {
                "seq": sequence,
                "type": "modify",
                "order_id": command.order_id,
                "price": command.price,
                "quantity": command.quantity,
            }
        case _:
            assert_never(command)
    return _dumps(record)


def decode_command(line: str) -> tuple[int, Command]:
    """The sequence number and command in one journal line.

    Raises:
        DecodeError: If the line is not a valid command record.
    """
    try:
        record = json.loads(line)
    except json.JSONDecodeError as error:
        raise DecodeError("not valid JSON") from error
    if not isinstance(record, dict):
        raise DecodeError("not a JSON object")
    kind = record.get("type")
    if not isinstance(kind, str) or kind not in _DECODERS:
        raise DecodeError(f"unknown command type {kind!r}")
    fields, build = _DECODERS[kind]
    expected = {"seq", "type", *fields}
    if set(record) != expected:
        raise DecodeError(f"expected fields {sorted(expected)}, found {sorted(record)}")
    return _integer(record, "seq"), build(record)


def _dumps(value: _Record) -> str:
    return json.dumps(value, separators=(",", ":"))


def _decode_rules(value: object) -> MarketRules:
    if not isinstance(value, dict) or set(value) != {"price_band"}:
        raise DecodeError(f"market rules must hold exactly price_band, found {value!r}")
    band = value["price_band"]
    if band is None:
        return MarketRules()
    if not isinstance(band, dict) or set(band) != {"lower", "upper"}:
        raise DecodeError(f"market rules: price_band needs lower and upper, found {band!r}")
    try:
        return MarketRules(
            price_band=PriceBand(lower=_integer(band, "lower"), upper=_integer(band, "upper"))
        )
    except ValueError as error:
        raise DecodeError(f"market rules: {error}") from error


def _integer(record: _Record, key: str) -> int:
    value = record[key]
    # bool is a subclass of int in Python, and true is not a quantity.
    if type(value) is not int:
        raise DecodeError(f"{key} must be an integer, found {value!r}")
    return value


def _side(record: _Record) -> Side:
    value = record["side"]
    try:
        return Side(value)
    except ValueError as error:
        raise DecodeError(f"side must be 'buy' or 'sell', found {value!r}") from error


def _validity(record: _Record) -> Validity:
    value = record["validity"]
    try:
        return Validity(value)
    except ValueError as error:
        raise DecodeError(f"validity must be 'day' or 'ioc', found {value!r}") from error


def _limit(record: _Record) -> Command:
    return NewLimitOrder(
        side=_side(record),
        price=_integer(record, "price"),
        quantity=_integer(record, "quantity"),
        validity=_validity(record),
    )


def _market(record: _Record) -> Command:
    return NewMarketOrder(side=_side(record), quantity=_integer(record, "quantity"))


def _cancel(record: _Record) -> Command:
    return CancelOrder(order_id=_integer(record, "order_id"))


def _modify(record: _Record) -> Command:
    return ModifyOrder(
        order_id=_integer(record, "order_id"),
        price=_integer(record, "price"),
        quantity=_integer(record, "quantity"),
    )


_DECODERS: dict[str, tuple[tuple[str, ...], Callable[[_Record], Command]]] = {
    "limit": (("side", "price", "quantity", "validity"), _limit),
    "market": (("side", "quantity"), _market),
    "cancel": (("order_id",), _cancel),
    "modify": (("order_id", "price", "quantity"), _modify),
}
