# 4. Lint rules enforce the hygiene rules

Date: 19 September 2026. Status: proposed.

## Context

The project bans `print` debugging, commented-out code, bare `except` and TODO markers. A rule that
depends on someone remembering it eventually gets broken.

## Decision

ruff lints and formats. Alongside the usual correctness sets, four rule sets turn the hygiene rules
into build failures:

| Rule set | Blocks |
|---|---|
| `T20` | `print` statements |
| `ERA` | commented-out code |
| `BLE` | blind `except` clauses |
| `FIX` | `TODO`, `FIXME` and `XXX` markers |

Tests may use `assert` and literal numbers, and they skip docstrings, because a test's name is its
documentation.

## Consequences

A breach fails CI rather than waiting for review. A genuine exception needs a targeted
`# noqa: <code>` with a reason on the line above it, which keeps every exception visible and
searchable.
