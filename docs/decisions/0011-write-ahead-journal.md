# 11. The journal is a write-ahead log of commands

Date: 19 September 2026. Status: proposed.

## Context

The brief asks for an append-only log of every input, such that replay rebuilds identical state.
Two questions: what goes in the log, and does it get written before or after the engine acts?

## Decision

- **Log commands, not events.** The engine is deterministic, so commands alone rebuild everything,
  and they are the smaller record. Events can be regenerated at any time by replay.
- **Write before applying.** Each command is written and flushed before the engine sees it. If the
  write fails, the command is not applied. If the process dies mid-write, the torn record belongs to
  a command that never ran. The engine is never ahead of its journal.
- **Record everything, including commands that will be rejected**, because rejected new orders use
  up order IDs.
- **Refuse, never guess, on damage.** The reader checks the header and version, every field and its
  type, sequence numbers with no gaps, and a final record cut off mid-write. Any problem raises
  `JournalError` naming the line. The writer runs the same checks before appending to an existing
  file.
- **Recover by replay.** Opening a journaled engine on an existing journal replays it, then keeps
  appending.

## Consequences

- Restart time grows with the length of the journal. Snapshots would fix that, and they are not
  built yet.
- A torn final record currently stops recovery with an error, and a person has to truncate it.
  Dropping it automatically is safe under write-ahead ordering, but it is a policy call, so it was
  left out for now.
- The on-disk format and the fsync policy are provisional. Both are open questions.
