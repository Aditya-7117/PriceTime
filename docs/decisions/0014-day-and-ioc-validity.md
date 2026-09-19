# 14. DAY and IOC validity

Date: 19 September 2026. Status: accepted.

## Context

A limit order that cannot fill at once either waits on the book or goes away. NSE offers two time
conditions on its orders: DAY and IOC (immediate or cancel).

## Decision

Every new order carries a validity, DAY by default.

- **DAY** rests whatever does not fill, until it fills, is cancelled or the session ends.
- **IOC** trades what it can at once, and the remainder is cancelled with reason `UNFILLED_IOC`. An
  IOC order never rests.

## Consequences

- Resting orders are always DAY, so the resting order record does not store validity.
- The session boundary is one engine's lifetime ([decision 11](0011-one-engine-per-instrument.md)).
  DAY orders expire with the engine rather than through an explicit end-of-day command.
- Market orders take a validity too, because NSE's market price protection treats a DAY and an IOC
  market order differently ([decision 15](0015-market-price-protection.md)).
