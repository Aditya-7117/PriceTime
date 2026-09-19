# 7. No clock in the engine: time priority is command order

Date: 19 September 2026. Status: proposed.

## Context

Price-time priority needs "earlier". A wall-clock timestamp is the obvious source, but a clock
gives a different answer on every run, so replay could not land in identical state. Two orders can
also share a timestamp.

## Decision

The engine reads no clock and no randomness. It counts the commands it has processed. An order's
`priority` is the sequence number of the command that gave it its current queue position. A new
order gets the sequence number of its own command. A modify that loses priority gets the modify's
sequence number.

## Consequences

- Time priority is exact and has no ties.
- The same commands always produce the same book, the same IDs and the same events.
- Real timestamps, when they are needed for execution reports, belong at the edge: stamped by
  whatever sequences commands into the engine, and recorded in the journal alongside them.
