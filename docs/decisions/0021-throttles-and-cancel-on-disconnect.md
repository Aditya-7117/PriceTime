# 21. Each session has its own limits

Date: 20 September 2026. Status: accepted.

## Context

Two things an exchange does that a matching engine does not: it limits how fast one member may send
orders, and it decides what happens to a member's resting orders when their connection dies. Both
are per-session policy, and both are things an interviewer expects an exchange to have thought
about.

## Decision

Two settings per session, both off by default.

**An order rate limit**, as a token bucket: a sustained rate in orders per second and a burst size.
An order that exceeds it is rejected with a business reject, not queued, because queuing hides the
overload from the sender and turns it into latency for everybody else.

**Cancel on disconnect.** When the connection drops without a logout, every resting order belonging
to that session is cancelled, and the cancels are journaled like any other command. A clean logout
does not trigger it: a member who says goodbye keeps their orders, which is how the options are
usually offered.

## Consequences

- A runaway client is refused rather than allowed to fill the queue.
- A member whose line dies does not leave orders on the book that they can no longer manage, which
  is the case cancel-on-disconnect exists for.
- The cancels go through the journal, so a replay reproduces them and the book after recovery is
  the same book.
- Both are per-session settings in the exchange's configuration, so different members can have
  different arrangements, as they do in practice.
