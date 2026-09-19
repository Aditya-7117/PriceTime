# 13. Market rules per engine, starting with the daily price band

Date: 19 September 2026. Status: accepted.

## Context

The reference venue is NSE. It publishes a price band for each stock each day, and rejects any order
priced outside it. Rules like this vary by instrument and by day, while the engine has to stay
deterministic: a replay must reject exactly what the original run rejected.

## Options

- **Check bands in the gateway, before the engine.** Keeps the engine smaller, but a replay of the
  journal would no longer reproduce the rejections, and the engine could not be tested against its
  own rules.
- **Give the engine a rules object, fixed for its life, and record it in the journal header.**

## Decision

The engine takes a `MarketRules` value at construction. It is immutable for the life of the engine,
which covers one instrument for one session. It holds the price band, inclusive at both ends, or none.
A new limit order priced outside the band is rejected with `PRICE_OUT_OF_BAND`, and so is a modify.
The journal's header records the rules. Replay constructs its engine from the header, and reopening a
journal under different rules is refused.

## Consequences

- Every rule that changes the engine's output lives in one value, and that value travels with the
  journal.
- A band that changes during the session would need a command of its own. That is not modelled.
- Tick size is not in the rules. The engine sees whole ticks, and the edge refuses prices off the tick
  grid ([decision 10](0010-prices-are-integer-ticks.md)).
