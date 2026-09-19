# 9. Order IDs are issued by the engine

Date: 19 September 2026. Status: accepted.

## Context

A cancel or modify has to name the order it acts on. In FIX, the client names each order with its
own `ClOrdID` (tag 11), unique only within its session, and the exchange assigns `OrderID` (tag 37).

## Options

- **The engine issues IDs** 1, 2, 3 and so on, in the order new-order commands arrive.
- **The caller supplies IDs** and the engine rejects duplicates.

## Decision

The engine issues them, including for new orders it rejects. This is the exchange's side of the FIX
split: the engine owns tag 37, and the gateway maps each session's tag 11 onto it.

## Consequences

- Duplicate IDs cannot happen, so there is no duplicate check.
- An ID below the next one to be issued was issued at some point. Telling an order that has left
  the book apart from one that never existed needs only that counter, not a record of every
  finished order.
- A new-order line in the journal does not name the ID it created. Replay recreates it.
