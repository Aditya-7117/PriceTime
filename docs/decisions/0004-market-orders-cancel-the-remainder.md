# 4. Market orders cancel whatever they cannot fill

Date: 19 September 2026. Status: superseded by [decision 15](0015-market-price-protection.md).

## Context

A market order has no price, so if the book runs out before it fills, the rest has nowhere to rest.

## Options

- **Cancel the remainder** (immediate or cancel behaviour).
- **Rest the remainder at the last traded price**, as a limit order. Some venues do this. It invents
  a price the owner never gave.
- **Reject the order if the book cannot fill it all** (fill or kill behaviour).

## Decision

Cancel the remainder. The engine reports `OrderCancelled` with reason `NO_LIQUIDITY` and the unfilled
quantity. A market order never rests.

## Consequences

Every share of a market order is accounted for as filled or cancelled. There is no price protection:
a market order into a thin book trades at whatever prices are there. Real venues add price bands for
this, and those are out of scope.
