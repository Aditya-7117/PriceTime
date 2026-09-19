# 3. mypy in strict mode for type checking

Date: 19 September 2026. Status: proposed.

## Context

Every public function carries type hints, and a checker in strict mode must pass over `src/`.

## Options

- **mypy.** The reference checker. Installs from PyPI as a compiled wheel with no other runtime.
- **pyright.** Faster, with sharper inference. It runs on Node.js, which the PyPI package fetches on
  first use unless one is installed. One more moving part in CI.

## Decision

mypy with `strict = true`, plus `warn_unreachable` and the extra error codes `ignore-without-code`,
`possibly-undefined`, `redundant-expr` and `truthy-bool`. It checks `tests/` as well as `src/`,
because a test helper with the wrong type can make a test pass for the wrong reason.

## Consequences

Data decoded from JSON is typed as `object`, never `Any`, so the journal reader has to check the
type of every field before using it. That is extra code, and it is the point.
