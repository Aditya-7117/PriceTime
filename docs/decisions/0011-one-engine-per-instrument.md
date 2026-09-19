# 11. One engine and one journal per instrument

Date: 19 September 2026. Status: accepted.

## Context

An exchange lists many instruments. Orders in one instrument never interact with orders in another.

## Options

- **One engine and one journal per instrument**, with a router in front that picks the engine by
  symbol (FIX tag 55).
- **Separate engines sharing one journal**, which gives a single global sequence across symbols.
- **One engine for every instrument**, with a symbol on every command.

## Decision

One engine and one journal per instrument. Commands carry no symbol; the router in the FIX layer
owns the mapping.

## Consequences

- The engine stays simple and each instrument replays on its own.
- Instruments shard perfectly across processes or machines, which is how exchanges scale out.
- There is no total order across instruments. A matching engine does not need one.
