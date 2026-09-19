# 2. Lock dependencies with pip-tools, with hashes

Date: 19 September 2026. Status: proposed.

## Context

The project must install from a clean machine using the README alone, with every dependency
pinned. There are no runtime dependencies. The development tools (pytest, hypothesis, mypy, ruff)
still need a lockfile.

## Options

- **pip-tools.** Compiles `pyproject.toml` into `requirements-dev.lock` with an exact version and
  SHA-256 hashes for every package. Installing needs nothing but Python and pip.
- **uv.** Much faster, and `uv.lock` is becoming the default. It is one more tool a reviewer has to
  install before anything works.
- **Poetry.** Its own lockfile and workflow. Heavier than the problem needs.

## Decision

pip-tools, compiled with `--generate-hashes --allow-unsafe`. Installing uses
`pip install --require-hashes`, so a tampered or substituted package fails the install. The
development tools live in the `dev` extra in `pyproject.toml`, because pip-tools cannot yet read
PEP 735 dependency groups.

## Consequences

Regenerate the lock with:

    pip-compile --extra dev --generate-hashes --allow-unsafe --strip-extras \
        --output-file requirements-dev.lock pyproject.toml

Moving to uv later takes a few minutes and changes no code.
