# 19. A session's state outlives the process

Date: 20 September 2026. Status: accepted.

## Context

FIX sequence numbers are the protocol's memory. Each side counts the messages it has sent, and a
gap tells the other side that something was lost. If a session restarts and starts counting again
from one, the counterparty sees a sequence number lower than it expects, which under FIX 4.4 is not
recoverable in-session: the connection has to be dropped.

A resend request also needs the messages themselves, not only the numbers.

## Options

1. **In memory only.** Simple. Every restart forces both sides to reset their sequence numbers,
   which is what the protocol has a big red switch for, and which a real counterparty would not
   accept mid-day.
2. **Persist the sequence numbers.** A restart continues the conversation, but a resend request for
   a message sent before the restart cannot be answered with the message itself.
3. **Persist the sequence numbers and the messages sent.** A restart continues the conversation and
   can answer a resend request properly.

## Decision

Option 3. `FileStore` appends each sent message and each sequence number change to a file and
forces it to disk; on startup it reads the file back. A session that reconnects after either side
has restarted picks up where it left off, and a resend request is answered with the original
messages, marked `PossDupFlag=Y` with their original sending times.

## Consequences

- A restart of the exchange is invisible to a client beyond the reconnection itself.
- The store grows with the session. It is a day's file, deleted between sessions, as a FIX store
  normally is.
- Recovery reads two things: the journal, which rebuilds the book, and the session store, which
  rebuilds the conversation. They are separate files with separate lifetimes, because they answer
  different questions.
- An in-memory store is kept for tests and for the benchmark, where durability is not the subject.
