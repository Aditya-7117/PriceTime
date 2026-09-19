"""The gateway: FIX requests in, engine commands out, execution reports back."""

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from pricetime.fix import tags
from pricetime.fix.config import ExchangeConfig, Instrument, SessionSettings
from pricetime.fix.gateway import Gateway, Outbound
from pricetime.fix.tags import (
    CxlRejReason,
    ExecType,
    FixSide,
    MsgType,
    OrdRejReason,
    OrdStatus,
    OrdType,
    SelfTradeInstruction,
    TimeInForce,
)
from pricetime.fix.wire import Fields
from pricetime.rules import MarketRules, PriceBand

SYMBOL = "DEMOCO"
TICK = Decimal("0.05")
FIRST = "CLIENT1"
SECOND = "CLIENT2"
START = datetime(2026, 9, 21, 9, 15, tzinfo=UTC)

# A stock around 100 rupees, priced in 0.05 ticks: the band runs from 1,800
# ticks (90.00) to 2,200 (110.00), and market orders may trade 5% from the last
# trade, which opens at 2,000 ticks (100.00).
RULES = MarketRules(
    price_band=PriceBand(lower=1_800, upper=2_200),
    protection_bps=500,
    protection_min_ticks=2,
    opening_price=2_000,
)


def at(seconds: float) -> datetime:
    return START + timedelta(seconds=seconds)


@pytest.fixture
def gateway(tmp_path: Path) -> Iterator[Gateway]:
    """An exchange with one instrument, two accounts and two sessions."""
    with _exchange(tmp_path) as ready:
        yield ready


def _config(
    tmp_path: Path, *, throttle: int = 0, cancel_on_disconnect: bool = False
) -> ExchangeConfig:
    return ExchangeConfig(
        comp_id="PRICETIME",
        instruments={
            SYMBOL: Instrument(
                symbol=SYMBOL, journal=tmp_path / f"{SYMBOL}.jsonl", rules=RULES, tick_size=TICK
            )
        },
        accounts={"ACC-1": 1, "ACC-2": 2},
        sessions={
            FIRST: SessionSettings(
                comp_id=FIRST,
                store=tmp_path / "first.jsonl",
                orders_per_second=throttle,
                burst=throttle,
                cancel_on_disconnect=cancel_on_disconnect,
            ),
            SECOND: SessionSettings(comp_id=SECOND, store=tmp_path / "second.jsonl"),
        },
    )


def _exchange(tmp_path: Path, **settings: object) -> "_Closing":
    return _Closing(Gateway(_config(tmp_path, **settings)))  # type: ignore[arg-type]


class _Closing:
    """A gateway that closes its journals when the test finishes with it."""

    def __init__(self, gateway: Gateway) -> None:
        self.gateway = gateway

    def __enter__(self) -> Gateway:
        return self.gateway

    def __exit__(self, *_: object) -> None:
        self.gateway.close()


def new_order(
    client_order_id: str,
    side: FixSide = FixSide.BUY,
    price: str | None = "100.00",
    quantity: int = 10,
    *,
    account: str = "ACC-1",
    symbol: str = SYMBOL,
    time_in_force: str = TimeInForce.DAY,
    self_trade: SelfTradeInstruction | None = None,
) -> Fields:
    fields: list[tuple[int, str]] = [
        (tags.MSG_TYPE, MsgType.NEW_ORDER_SINGLE),
        (tags.CL_ORD_ID, client_order_id),
        (tags.ACCOUNT, account),
        (tags.SYMBOL, symbol),
        (tags.SIDE, side),
        (tags.ORDER_QTY, str(quantity)),
        (tags.ORD_TYPE, OrdType.LIMIT if price is not None else OrdType.MARKET),
        (tags.TIME_IN_FORCE, time_in_force),
        (tags.TRANSACT_TIME, "20260921-09:15:00.000"),
    ]
    if price is not None:
        fields.append((tags.PRICE, price))
    if self_trade is not None:
        fields.append((tags.SELF_TRADE_INSTRUCTION, self_trade))
    return tuple(fields)


def cancel(client_order_id: str, original: str, symbol: str = SYMBOL) -> Fields:
    return (
        (tags.MSG_TYPE, MsgType.ORDER_CANCEL_REQUEST),
        (tags.CL_ORD_ID, client_order_id),
        (tags.ORIG_CL_ORD_ID, original),
        (tags.SYMBOL, symbol),
        (tags.SIDE, FixSide.BUY),
        (tags.TRANSACT_TIME, "20260921-09:15:00.000"),
    )


def replace(client_order_id: str, original: str, price: str, quantity: int) -> Fields:
    return (
        (tags.MSG_TYPE, MsgType.ORDER_CANCEL_REPLACE_REQUEST),
        (tags.CL_ORD_ID, client_order_id),
        (tags.ORIG_CL_ORD_ID, original),
        (tags.SYMBOL, SYMBOL),
        (tags.SIDE, FixSide.BUY),
        (tags.ORD_TYPE, OrdType.LIMIT),
        (tags.PRICE, price),
        (tags.ORDER_QTY, str(quantity)),
        (tags.TRANSACT_TIME, "20260921-09:15:00.000"),
    )


def one(outbound: list[Outbound]) -> dict[int, str]:
    (only,) = outbound
    return dict(only.fields)


def parts(outbound: list[Outbound]) -> list[tuple[str, str]]:
    """Each message as (session, ExecType or MsgType), for checking who hears what."""
    return [
        (item.session, dict(item.fields).get(tags.EXEC_TYPE, dict(item.fields)[tags.MSG_TYPE]))
        for item in outbound
    ]


def test_a_new_order_is_acknowledged(gateway: Gateway) -> None:
    report = one(gateway.handle(FIRST, new_order("A-1"), at(0)))

    assert report[tags.MSG_TYPE] == MsgType.EXECUTION_REPORT
    assert report[tags.EXEC_TYPE] == ExecType.NEW
    assert report[tags.ORD_STATUS] == OrdStatus.NEW
    assert report[tags.CL_ORD_ID] == "A-1"
    assert report[tags.ORDER_ID] == f"{SYMBOL}:1"
    assert report[tags.PRICE] == "100.00"
    assert report[tags.LEAVES_QTY] == "10"
    assert report[tags.CUM_QTY] == "0"
    assert report[tags.ACCOUNT] == "ACC-1"


def test_a_trade_is_reported_to_both_sides(gateway: Gateway) -> None:
    gateway.handle(FIRST, new_order("A-1", FixSide.BUY, "100.00", 10), at(0))

    outbound = gateway.handle(
        SECOND, new_order("B-1", FixSide.SELL, "100.00", 4, account="ACC-2"), at(1)
    )

    assert parts(outbound) == [
        (SECOND, ExecType.NEW),
        (FIRST, ExecType.TRADE),
        (SECOND, ExecType.TRADE),
    ]
    maker = dict(outbound[1].fields)
    assert maker[tags.CL_ORD_ID] == "A-1"
    assert maker[tags.LAST_QTY] == "4"
    assert maker[tags.LAST_PX] == "100.00"
    assert maker[tags.CUM_QTY] == "4"
    assert maker[tags.LEAVES_QTY] == "6"
    assert maker[tags.ORD_STATUS] == OrdStatus.PARTIALLY_FILLED
    assert maker[tags.AVG_PX] == "100.0000"


def test_the_average_price_covers_every_fill(gateway: Gateway) -> None:
    gateway.handle(FIRST, new_order("A-1", FixSide.SELL, "100.00", 5), at(0))
    gateway.handle(FIRST, new_order("A-2", FixSide.SELL, "101.00", 5), at(1))

    outbound = gateway.handle(
        SECOND, new_order("B-1", FixSide.BUY, "101.00", 10, account="ACC-2"), at(2)
    )

    taker = dict(outbound[-1].fields)
    assert taker[tags.CUM_QTY] == "10"
    assert taker[tags.AVG_PX] == "100.5000"
    assert taker[tags.ORD_STATUS] == OrdStatus.FILLED


@pytest.mark.parametrize(
    ("order", "reason", "text"),
    [
        (new_order("A-1", account="NOT-REGISTERED"), OrdRejReason.UNKNOWN_ACCOUNT, "registered"),
        (new_order("A-1", symbol="OTHER"), OrdRejReason.UNKNOWN_SYMBOL, "not traded"),
        (new_order("A-1", price="100.07"), OrdRejReason.OTHER, "tick grid"),
        (new_order("A-1", quantity=0), OrdRejReason.INCORRECT_QUANTITY, "positive"),
        (
            new_order("A-1", time_in_force="4"),  # fill or kill, which this exchange does not take
            OrdRejReason.UNSUPPORTED_CHARACTERISTIC,
            "time in force",
        ),
    ],
    ids=["account", "symbol", "tick grid", "quantity", "validity"],
)
def test_an_order_the_exchange_will_not_take_is_rejected(
    gateway: Gateway, order: Fields, reason: OrdRejReason, text: str
) -> None:
    report = one(gateway.handle(FIRST, order, at(0)))

    assert report[tags.EXEC_TYPE] == ExecType.REJECTED
    assert report[tags.ORD_STATUS] == OrdStatus.REJECTED
    assert report[tags.ORD_REJ_REASON] == reason
    assert text in report[tags.TEXT]
    assert report[tags.ORDER_ID] == "NONE"


def test_the_same_client_order_id_twice_is_rejected(gateway: Gateway) -> None:
    gateway.handle(FIRST, new_order("A-1"), at(0))

    report = one(gateway.handle(FIRST, new_order("A-1"), at(1)))

    assert report[tags.ORD_REJ_REASON] == OrdRejReason.DUPLICATE_ORDER


def test_an_order_outside_the_price_band_is_rejected_by_the_engine(gateway: Gateway) -> None:
    report = one(gateway.handle(FIRST, new_order("A-1", price="120.00"), at(0)))

    assert report[tags.EXEC_TYPE] == ExecType.REJECTED
    assert "band" in report[tags.TEXT]
    assert report[tags.ORDER_ID] == f"{SYMBOL}:1"  # the engine issued an ID before refusing


def test_a_market_order_is_taken_without_a_price(gateway: Gateway) -> None:
    gateway.handle(FIRST, new_order("A-1", FixSide.SELL, "100.00", 10), at(0))

    outbound = gateway.handle(
        SECOND, new_order("B-1", FixSide.BUY, None, 4, account="ACC-2"), at(1)
    )

    accepted = dict(outbound[0].fields)
    assert accepted[tags.ORD_TYPE] == OrdType.MARKET
    assert tags.PRICE not in accepted
    assert dict(outbound[-1].fields)[tags.EXEC_TYPE] == ExecType.TRADE


def test_a_cancel_is_reported_against_the_cancel_request(gateway: Gateway) -> None:
    gateway.handle(FIRST, new_order("A-1"), at(0))

    report = one(gateway.handle(FIRST, cancel("A-2", "A-1"), at(1)))

    assert report[tags.EXEC_TYPE] == ExecType.CANCELED
    assert report[tags.ORD_STATUS] == OrdStatus.CANCELED
    assert report[tags.CL_ORD_ID] == "A-2"
    assert report[tags.ORIG_CL_ORD_ID] == "A-1"
    assert report[tags.LEAVES_QTY] == "0"


def test_cancelling_an_order_nobody_knows_is_refused(gateway: Gateway) -> None:
    refusal = one(gateway.handle(FIRST, cancel("A-2", "never-sent"), at(0)))

    assert refusal[tags.MSG_TYPE] == MsgType.ORDER_CANCEL_REJECT
    assert refusal[tags.CXL_REJ_REASON] == CxlRejReason.UNKNOWN_ORDER
    assert refusal[tags.ORDER_ID] == "NONE"


def test_cancelling_an_order_that_already_filled_is_too_late(gateway: Gateway) -> None:
    gateway.handle(FIRST, new_order("A-1", FixSide.BUY, "100.00", 10), at(0))
    gateway.handle(SECOND, new_order("B-1", FixSide.SELL, "100.00", 10, account="ACC-2"), at(1))

    refusal = one(gateway.handle(FIRST, cancel("A-2", "A-1"), at(2)))

    assert refusal[tags.MSG_TYPE] == MsgType.ORDER_CANCEL_REJECT
    assert refusal[tags.CXL_REJ_REASON] == CxlRejReason.TOO_LATE_TO_CANCEL


def test_a_replace_reports_the_new_client_order_id(gateway: Gateway) -> None:
    gateway.handle(FIRST, new_order("A-1", FixSide.BUY, "100.00", 10), at(0))

    report = one(gateway.handle(FIRST, replace("A-2", "A-1", "99.00", 8), at(1)))

    assert report[tags.EXEC_TYPE] == ExecType.REPLACED
    assert report[tags.CL_ORD_ID] == "A-2"
    assert report[tags.ORIG_CL_ORD_ID] == "A-1"
    assert report[tags.PRICE] == "99.00"
    assert report[tags.ORDER_QTY] == "8"
    assert "back of the queue" in report[tags.TEXT]


def test_a_replace_that_only_shrinks_keeps_its_place(gateway: Gateway) -> None:
    gateway.handle(FIRST, new_order("A-1", FixSide.BUY, "100.00", 10), at(0))

    report = one(gateway.handle(FIRST, replace("A-2", "A-1", "100.00", 4), at(1)))

    assert "kept its place" in report[tags.TEXT]


def test_a_later_cancel_can_name_the_replacement(gateway: Gateway) -> None:
    gateway.handle(FIRST, new_order("A-1", FixSide.BUY, "100.00", 10), at(0))
    gateway.handle(FIRST, replace("A-2", "A-1", "99.00", 8), at(1))

    report = one(gateway.handle(FIRST, cancel("A-3", "A-2"), at(2)))

    assert report[tags.EXEC_TYPE] == ExecType.CANCELED
    assert report[tags.ORIG_CL_ORD_ID] == "A-2"


def test_self_trade_prevention_is_carried_from_the_order(gateway: Gateway) -> None:
    gateway.handle(FIRST, new_order("A-1", FixSide.SELL, "100.00", 5), at(0))

    outbound = gateway.handle(
        FIRST,
        new_order("A-2", FixSide.BUY, "100.00", 5, self_trade=SelfTradeInstruction.CANCEL_ACTIVE),
        at(1),
    )

    cancelled = dict(outbound[-1].fields)
    assert cancelled[tags.EXEC_TYPE] == ExecType.CANCELED
    assert "self-trade" in cancelled[tags.TEXT]


def test_a_message_type_the_exchange_does_not_take_is_refused(gateway: Gateway) -> None:
    refusal = one(gateway.handle(FIRST, ((tags.MSG_TYPE, "AB"), (tags.CL_ORD_ID, "A-1")), at(0)))

    assert refusal[tags.MSG_TYPE] == MsgType.BUSINESS_MESSAGE_REJECT
    assert refusal[tags.REF_MSG_TYPE] == "AB"


def test_orders_beyond_the_rate_limit_are_refused(tmp_path: Path) -> None:
    with _exchange(tmp_path, throttle=1) as gateway:
        first = one(gateway.handle(FIRST, new_order("A-1"), at(0)))
        second = one(gateway.handle(FIRST, new_order("A-2"), at(0)))
        later = one(gateway.handle(FIRST, new_order("A-3"), at(5)))

    assert first[tags.EXEC_TYPE] == ExecType.NEW
    assert second[tags.EXEC_TYPE] == ExecType.REJECTED
    assert "rate limit" in second[tags.TEXT]
    assert later[tags.EXEC_TYPE] == ExecType.NEW


def test_a_dropped_connection_cancels_that_sessions_orders_when_asked(tmp_path: Path) -> None:
    with _exchange(tmp_path, cancel_on_disconnect=True) as gateway:
        gateway.handle(FIRST, new_order("A-1"), at(0))
        gateway.handle(SECOND, new_order("B-1", account="ACC-2"), at(1))

        outbound = gateway.disconnected(FIRST, at(2))

        assert parts(outbound) == [(FIRST, ExecType.CANCELED)]
        assert gateway.disconnected(SECOND, at(3)) == []


def test_a_dropped_connection_leaves_orders_alone_by_default(gateway: Gateway) -> None:
    gateway.handle(FIRST, new_order("A-1"), at(0))

    assert gateway.disconnected(FIRST, at(1)) == []


def test_a_restarted_gateway_still_knows_whose_orders_it_holds(tmp_path: Path) -> None:
    with _exchange(tmp_path) as gateway:
        gateway.handle(FIRST, new_order("A-1", FixSide.BUY, "100.00", 10), at(0))
        gateway.handle(SECOND, new_order("B-1", FixSide.SELL, "100.00", 4, account="ACC-2"), at(1))

    with _exchange(tmp_path) as restarted:
        report = one(restarted.handle(FIRST, cancel("A-2", "A-1"), at(2)))

    assert report[tags.EXEC_TYPE] == ExecType.CANCELED
    assert report[tags.CL_ORD_ID] == "A-2"
    assert report[tags.CUM_QTY] == "4"  # the fill from before the restart is still counted
    assert report[tags.LEAVES_QTY] == "0"


@pytest.mark.parametrize(
    ("bad_replace", "text"),
    [
        (replace("A-2", "A-1", "99.00", 0), "positive"),
        (replace("A-2", "A-1", "99.07", 5), "tick grid"),
    ],
    ids=["quantity", "tick grid"],
)
def test_a_replacement_the_exchange_cannot_read_is_refused(
    gateway: Gateway, bad_replace: Fields, text: str
) -> None:
    gateway.handle(FIRST, new_order("A-1"), at(0))

    refusal = one(gateway.handle(FIRST, bad_replace, at(1)))

    assert refusal[tags.MSG_TYPE] == MsgType.ORDER_CANCEL_REJECT
    assert refusal[tags.CXL_REJ_REASON] == CxlRejReason.OTHER
    assert text in refusal[tags.TEXT]
    assert refusal[tags.ORDER_ID] == f"{SYMBOL}:1"


def test_replacing_an_order_nobody_knows_is_refused(gateway: Gateway) -> None:
    refusal = one(gateway.handle(FIRST, replace("A-2", "never-sent", "99.00", 5), at(0)))

    assert refusal[tags.CXL_REJ_REASON] == CxlRejReason.UNKNOWN_ORDER


def test_a_market_order_before_the_first_trade_is_refused(tmp_path: Path) -> None:
    quiet = ExchangeConfig(
        comp_id="PRICETIME",
        instruments={
            SYMBOL: Instrument(
                symbol=SYMBOL,
                journal=tmp_path / "quiet.jsonl",
                rules=MarketRules(protection_bps=500, protection_min_ticks=2),
                tick_size=TICK,
            )
        },
        accounts={"ACC-1": 1},
        sessions={FIRST: SessionSettings(comp_id=FIRST, store=tmp_path / "first.jsonl")},
    )
    gateway = Gateway(quiet)
    try:
        report = one(gateway.handle(FIRST, new_order("A-1", FixSide.BUY, None, 5), at(0)))
    finally:
        gateway.close()

    assert report[tags.EXEC_TYPE] == ExecType.REJECTED
    assert "no trade yet today" in report[tags.TEXT]
