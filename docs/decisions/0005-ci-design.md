# 5. CI: pinned actions, a version matrix, derandomized property tests

Date: 19 September 2026. Status: proposed.

## Context

CI runs lint, format, types and tests on every push, and it must be green. A property-based test
that fails once in CI and passes on the rerun is worse than no test, because people learn to
ignore it.

## Decision

- Lint, format and types run once, on 3.12. Tests run on 3.12, 3.13 and 3.14.
- Actions are pinned to full commit hashes, with the release tag in a comment. A tag can be moved
  to point at different code; a commit hash cannot.
- CI sets `HYPOTHESIS_PROFILE=ci`: 500 examples per property and `derandomize=True`, so every run
  generates the same inputs and a red build reproduces exactly. Local runs use 200 random
  examples, so each run explores inputs the last one did not.

## Consequences

CI will not find a new counterexample on its own. New inputs come from local runs. The trade is
deliberate: a reproducible build over a search that happens to run in CI.
