# 16. Self-trade prevention, as NSE's check works

Date: 19 September 2026. Status: accepted.

## Context

A self-trade is an order trading against another order from the same client. No ownership
changes hands, and regulators treat such trades as possible wash trading, which can fake volume.
NSE prevents them with its self-trade prevention check, keyed on the client's identity (PAN), and
lets the member choose at order entry whether the incoming (active) or the resting (passive) order
is cancelled.

## Options

- **Identity:** the connection (FIX `SenderCompID`), an account, or a client ID on every order.
- **Action:** one fixed policy for the whole engine, or a choice carried by each order. Policies
  seen at venues include cancelling the newest, the oldest, both, or decrementing both.

## Decision

Every new order carries a `client_id`, which the FIX layer will fill from the order's account, and
a `self_trade` choice that defaults to cancel active.

- **Cancel active:** when the incoming order reaches a resting order from its own client, matching
  stops and the incoming order's remainder is cancelled with reason `SELF_TRADE`. Trades it made
  before that point stand. The resting order is untouched.
- **Cancel passive:** the resting order is cancelled with reason `SELF_TRADE`, and the incoming order
  keeps matching behind it.

The choice belongs to whichever order is incoming, including a resting order that a modify sends
back into the book.

## Consequences

- No trade ever has the same client on both sides. A property checks this after every command.
- Cancelled quantity from self-trade prevention is one more outflow in the conservation ledger, so
  the conservation invariant needed no change.
- Two orders from one client at the same price on opposite sides cannot both rest, because the
  second always meets the first.
