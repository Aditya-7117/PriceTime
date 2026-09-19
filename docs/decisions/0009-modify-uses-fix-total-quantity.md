# 9. Modify takes a new total quantity, as FIX does, and keeps priority only when shrinking

Date: 19 September 2026. Status: proposed.

## Context

A modify changes an order's price, its size, or both. Two questions follow. Does the new quantity
mean the new total or the new open amount? And when does the order keep its place in the queue?

## Decision

**Quantity is the new total, including anything already filled.** This is how the FIX 4.4
cancel/replace request defines `OrderQty`. It matters when a modify races a fill: the owner has 10
open, asks to change to 8, and 4 fill while the request is in flight. With total quantity the
answer is 4 open and 8 in all, which is what the owner asked for. Had 8 meant the open amount, the
owner would end with 12, more than they ever asked for.

**Priority is kept only when the price is unchanged and the open quantity does not grow.** A
reduction takes nothing from the orders queued behind it. A larger size would jump ahead of orders
that arrived first, and a new price means a new queue. Either one sends the order to the back of the
queue at its new price as a new arrival, and it trades at once if the new price crosses the spread.

**Asking for no more than has already filled finishes the order.** Its open quantity goes to zero
and it leaves the book. The `OrderModified` event carries the requested total and `remaining=0`.

## Consequences

- The FIX layer maps a cancel/replace straight onto this command without translating quantities.
- The exact execution report FIX expects when a replace cuts quantity to or below the filled amount
  has not been checked against the specification yet. That check belongs with the FIX session work.
- The order keeps its ID across a modify. Queue position is carried by the priority number, not the
  ID, so the ID never has to change.
