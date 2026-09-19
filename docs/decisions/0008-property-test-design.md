# 8. How the property tests are built

Date: 19 September 2026. Status: accepted.

## Context

The brief names four invariants: the book never crosses, shares are conserved, a cancel removes
exactly one order, and replay is deterministic. A property test is only as good as its inputs and
the independence of its checks.

## Decision

- **Check after every command, not just at the end.** A book that crosses for one command and then
  recovers is still a bug.
- **Check conservation against an independent ledger.** The ledger rebuilds each order's open and
  filled quantity from the commands and events alone, never from the engine's own counters. The book
  must then hold exactly the orders the ledger says are open, with exactly those quantities.
- **Make the generator reach the hard cases.** A first version drew cancel and modify IDs blindly,
  and a measurement over 500 sequences found 10 successful cancels, one modify that kept priority,
  and no book deeper than 10 orders. The generator now follows the IDs it has issued, draws buys and
  sells from overlapping price bands, and sometimes emits a same-price reduction. The same
  measurement then found 1,654 successful cancels, 315 priority-keeping modifies, 961 multi-level
  sweeps, and books of 10 orders or more in 296 of 500 sequences.
- **Prove the tests can fail.** Twelve deliberate bugs were planted in the engine, one at a time,
  among them trading at the taker's price, dropping a market order's remainder, a cancel that also
  removes the next order, and asks sorted the wrong way. The property suite caught all twelve.

Three more properties sit beside the brief's four: trades follow price-time priority, a modify keeps
its place only when it shrinks in place, and the levels, queue links and ID index always agree.

**A second, naive implementation as an oracle.** `tests/reference.py` implements the same rules as
plainly as possible: every resting order in one flat list, and the other side sorted from scratch
at every step of a match. No linked lists, no index, no sorted levels. The differential test runs
both on the same generated command sequences and requires identical events and identical state
after every command. Six planted bugs were each caught by the differential test on its own.

## Consequences

The mutation check was a one-off run, not part of CI. A mutation testing tool would make it
repeatable, at the cost of another dependency.
