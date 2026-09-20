# 17. The FIX session layer has no sockets in it

Date: 19 September 2026. Status: accepted.

## Context

FIX has two layers. The session layer keeps a conversation alive: logon, heartbeats, sequence
numbers, resends after a gap, logout. The application layer carries the orders. The session layer
is where the awkward cases live, and they are all about timing and failure: a message that arrives
out of order, a reply that never comes, a client that reconnects mid-conversation.

Code that reads from a socket and keeps session state in the same place can only be tested against
a real socket, with sleeps, and the interesting cases are the ones a socket will not reproduce on
demand.

## Options

1. **A session object that owns its connection.** Fewer moving parts, the obvious shape. Every test
   needs a real server, a real client and real time.
2. **Sans-IO: the session is a function of bytes and time.** Bytes and a timestamp go in, a list of
   actions comes out — send these bytes, deliver this message, the session is logged on, drop the
   line. Something else owns the socket and the clock.
3. **An off-the-shelf FIX engine** (QuickFIX and its Python binding). Complete and battle-tested,
   but then the project is a configuration of someone else's engine, which is not what this
   repository is for.

## Decision

Option 2. `FixSession` never touches a socket, never reads a clock and never sleeps. `ExchangeServer`
does the reading, writing and timing and hands the session what it needs.

`simplefix` is used for framing and checksums only, inside `fix/wire.py`, which is the only module
that imports it. Everything above it works in terms of tag/value pairs.

## Consequences

- Packet loss, reordering, duplicate logons and reconnections are ordinary unit tests: two sessions
  joined by a link the test controls, with no sockets and no waiting.
- Two real bugs were found this way rather than by inspection: a resend request that was never
  repeated after its reply was lost, leaving both sides waiting forever, and a logon that arrived
  ahead of its sequence number and hung the reconnection.
- The session layer can be driven by something other than asyncio later without rewriting it.
- The cost is one more layer: the server has to apply the actions the session returns, and a caller
  who forgets to apply one gets a session that quietly does nothing.
- Swapping `simplefix` for another codec means rewriting one small module.
