# 1. Toolchain and continuous integration

Date: 19 September 2026. Status: accepted.

## Context

The project has to install on a clean machine from the README alone, pin every dependency, pass
strict type checking and lint, and run its checks in CI on every push. A property-based test that
fails once and passes on rerun is worse than none, because people learn to ignore it.

## Decision

| Choice | Picked | Over | Why |
|---|---|---|---|
| Python version | 3.12 and later; CI tests 3.12, 3.13 and 3.14 | 3.11 | The `type` statement for aliases. Development and type checking run on the floor, so nothing newer slips in |
| Lockfile | pip-tools, `requirements-dev.lock` with SHA-256 hashes | uv, Poetry | Installing needs only Python and pip; a tampered package fails `--require-hashes` |
| Type checker | mypy, strict, over `src/` and `tests/` | pyright | No Node.js runtime in CI. JSON input is typed `object`, never `Any`, so every field is checked |
| Lint and format | ruff, including `T20`, `ERA`, `BLE` and `FIX` | review by eye | Print statements, commented-out code, blind excepts and TODO markers fail the build instead of relying on memory |
| CI actions | pinned to full commit hashes | version tags | A tag can be moved; a commit hash cannot |
| Property tests in CI | 500 examples, `derandomize=True` | random seeds | A red build reproduces exactly. Local runs stay random and keep exploring |

There are no runtime dependencies. The development tools live in the `dev` extra in
`pyproject.toml`, because pip-tools cannot read PEP 735 dependency groups.

## Consequences

- Any benchmark has to name the interpreter version, because CPython speed changes between
  releases.
- CI never finds a new counterexample on its own; local runs do.
- Regenerate the lock with
  `pip-compile --extra dev --generate-hashes --allow-unsafe --strip-extras --output-file requirements-dev.lock pyproject.toml`.
- A genuine lint exception needs a targeted `# noqa: <code>` with a reason on the line above it.
