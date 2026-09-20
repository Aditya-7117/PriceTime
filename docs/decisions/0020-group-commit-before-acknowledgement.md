# 20. Nothing is acknowledged before it is on disk

Date: 20 September 2026. Status: accepted.

## Context

[Decision 7](0007-write-ahead-journal.md) settled that a command is journaled before it is applied.
Over a socket there is a second question: when may the execution report go out? If a reply leaves
before the write reaches the disk, a power cut can leave a client holding an acknowledgement for an
order the exchange has forgotten. That is the one failure an exchange cannot explain away.

Forcing every single order to disk on its own is correct and slow: one `fsync` per order, and each
one costs far more than the matching did.

## Decision

Group commit. The server reads whatever has arrived, applies every command in that batch, forces
the journal to disk once, and only then writes any of the replies. A busy moment shares one disk
sync across many orders; a quiet one pays for a sync it could not avoid.

`force_to_disk` is configuration, on by default. Turning it off leaves the writes in the operating
system's hands, which survives this process crashing but not the machine. It exists so the
benchmark can show what durability costs, and for a test environment that does not need it.

## Consequences

- An acknowledgement means the order is recoverable, which is the property the whole journal exists
  for.
- Latency under load improves rather than degrades: the busier it is, the more orders share a sync.
- Measured on a laptop at 1,000 orders per second, forcing to disk costs about four microseconds at
  the median and shows up in the tail instead
  ([docs/benchmarks.md](../benchmarks.md)).
- The engine still applies commands one at a time. Batching is about when the disk and the socket
  are touched, not about how matching works.
