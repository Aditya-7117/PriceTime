# Decision records

One short record per design decision: the context, the options, what was chosen and what it costs.
A record that a later one replaces is marked `superseded`, and it says by which.

| # | Decision |
|---|---|
| [1](0001-toolchain-and-ci.md) | Toolchain and continuous integration |
| [2](0002-commands-in-events-out.md) | Commands in, events out, one command at a time |
| [3](0003-no-clock-in-the-engine.md) | No clock in the engine: time priority is command order |
| [4](0004-market-orders-cancel-the-remainder.md) | Market orders cancel whatever they cannot fill |
| [5](0005-modify-uses-fix-total-quantity.md) | Modify takes a new total quantity and keeps priority only when shrinking |
| [6](0006-rejections-are-events.md) | Rejections are events; exceptions are for bugs and I/O |
| [7](0007-write-ahead-journal.md) | The journal is a write-ahead log of commands |
| [8](0008-property-test-design.md) | How the property tests are built |
| [9](0009-order-ids-issued-by-the-engine.md) | Order IDs are issued by the engine |
| [10](0010-prices-are-integer-ticks.md) | Prices are integer ticks, converted at the edge |
| [11](0011-one-engine-per-instrument.md) | One engine and one journal per instrument |
| [12](0012-price-level-structures.md) | Price level structures, pending measurement |
