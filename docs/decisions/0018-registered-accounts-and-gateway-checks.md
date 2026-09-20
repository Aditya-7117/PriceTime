# 18. Accounts are registered before they can trade

Date: 19 September 2026. Status: accepted.

## Context

The engine takes a `client_id`, an integer that decides who owns an order and therefore what may
not trade with what ([decision 16](0016-self-trade-prevention.md)). A FIX order carries an
`Account` (tag 1), a string chosen by whoever sends the message. Something has to turn one into the
other, and decide what happens when an account is unknown.

On NSE, a client code is registered with the exchange through a member before it can be used. The
exchange does not learn about a client from an order.

## Decision

The exchange is configured with a mapping from account string to client ID. An order whose account
is not in that mapping is rejected with `OrdRejReason` 15, unknown account. Nothing creates an
account implicitly.

The gateway also checks, before any command reaches an engine: the symbol is one this exchange
trades, the side is a side, the quantity is a positive whole number, the order type is one that is
supported, a limit order has a price on the instrument's tick grid and inside the day's band, and
the time in force is one of DAY or IOC. Each refusal carries the FIX reason code for that case,
rather than a single catch-all.

## Consequences

- Self-trade prevention means something: two different sessions can trade for the same client, and
  they still cannot trade with each other.
- A typo in an account is a rejection, not a new anonymous trader.
- The configuration is the registry. There is no admin interface, which is the right size for this
  project and stated as a limitation in the README.
- The gateway holds the tick size per instrument, because converting a decimal price to ticks is
  the last place a price can be refused for being off the grid
  ([decision 10](0010-prices-are-integer-ticks.md)).
