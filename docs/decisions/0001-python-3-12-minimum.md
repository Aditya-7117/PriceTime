# 1. Python 3.12 is the minimum version

Date: 19 September 2026. Status: proposed.

## Context

The project is Python first. The minimum version decides which language features the code can use
and which interpreters a reviewer needs.

## Options

- **3.11.** Wider reach. Loses the `type` statement for aliases and `itertools.batched`.
- **3.12.** The `type` statement, better error messages, and `typing.override`. Released October
  2023, so any reviewer's machine or CI image has it.
- **3.14 only.** Newest interpreter. Shuts out anyone who has not upgraded.

## Decision

`requires-python = ">=3.12"`. Development and type checking run against 3.12, the floor, so nothing
newer slips in by accident. CI tests 3.12, 3.13 and 3.14.

## Consequences

Any benchmark has to name the interpreter version, because CPython speed changes between releases.
Raising the floor later is a one-line change. Lowering it would mean rewriting the `type` aliases.
