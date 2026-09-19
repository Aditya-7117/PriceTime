# 6. Rejections are events; exceptions are for bugs and I/O

Date: 19 September 2026. Status: accepted.

## Context

Clients send bad input: a zero quantity, a cancel for an order that has already filled, a modify
for an ID that never existed. The engine has to say no.

## Decision

Refusing a command is a normal outcome, reported as an event: `OrderRejected` for a new order,
`CancelRejected` and `ModifyRejected` for the others. Each carries a reason. Exceptions are kept for
programming errors, such as adding an order whose ID is already resting, and for I/O failures in the
journal.

A cancel or modify naming an order that is not on the book is `TOO_LATE` if the ID was ever
issued, and `UNKNOWN_ORDER` if it was not. Because the engine issues IDs in sequence, any ID below
the next one to be issued was issued at some point. The distinction needs no record of finished
orders, so memory does not grow with the number of orders ever seen. These two reasons correspond
to FIX `CxlRejReason` 0 (too late to cancel) and 1 (unknown order).

## Consequences

- Rejections are recorded and replayed like everything else. A rejected new order still uses up an
  order ID, so replay has to see it to hand out the same IDs.
- Validation only covers what the engine can judge: positive prices and quantities, and live
  orders. Tick sizes, lot sizes and price bands would sit in front of the engine.
