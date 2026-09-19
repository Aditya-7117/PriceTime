# Decision records

One short record per design decision: the context, the options, what was chosen and what it costs.
Status is `proposed` until reviewed, then `accepted`, or `superseded` by a later record.

| # | Decision |
|---|---|
| [1](0001-python-3-12-minimum.md) | Python 3.12 is the minimum version |
| [2](0002-lock-dependencies-with-pip-tools.md) | Lock dependencies with pip-tools, with hashes |
| [3](0003-mypy-strict.md) | mypy in strict mode for type checking |
| [4](0004-lint-rules-enforce-hygiene.md) | Lint rules enforce the hygiene rules |
| [5](0005-ci-design.md) | CI: pinned actions, a version matrix, derandomized property tests |
| [6](0006-commands-in-events-out.md) | Commands in, events out, one command at a time |
| [7](0007-no-clock-in-the-engine.md) | No clock in the engine: time priority is command order |
| [8](0008-market-orders-cancel-the-remainder.md) | Market orders cancel whatever they cannot fill |
| [9](0009-modify-uses-fix-total-quantity.md) | Modify takes a new total quantity and keeps priority only when shrinking |
| [10](0010-rejections-are-events.md) | Rejections are events; exceptions are for bugs and I/O |
| [11](0011-write-ahead-journal.md) | The journal is a write-ahead log of commands |
| [12](0012-property-test-design.md) | How the property tests are built |
