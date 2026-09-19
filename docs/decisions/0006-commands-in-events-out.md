# 6. Commands in, events out, one command at a time

Date: 19 September 2026. Status: proposed.

## Context

The engine has to be deterministic, so that a replayed log lands in identical state. It also has to
tell the outside world what happened: acknowledgements, fills, cancels, rejections.

## Options

- **Callbacks.** The engine calls a listener for each fill as it happens. Simple to wire up, but the
  listener runs in the middle of a match. If it calls back into the engine, for example to cancel an
  order, it edits a price level that the matching loop is still walking.
- **Commands in, events out.** `process(command)` runs one command to completion and returns the
  list of events it caused. No outside code runs until the command has finished.
- **A thread or queue per side.** Concurrency the problem does not need, and it gives up
  determinism.

## Decision

Commands in, events out, one command at a time, on one thread. Commands and events are frozen
dataclasses.

## Consequences

- Re-entrancy is impossible by construction, not by care.
- "A cancel arriving for an order being matched" cannot happen inside the engine. The cancel is
  processed either before the aggressive order, and removes the whole resting order, or after it,
  and then finds the order filled (rejected as too late) or partly filled (only the open remainder
  is cancelled). The race moves to the edge of the system, where the sequencer decides the order of
  arrival, and that is where it belongs.
- Throughput is bounded by one core. That is the standard design for a matching engine: LMAX, for
  one, runs its business logic on a single thread.
