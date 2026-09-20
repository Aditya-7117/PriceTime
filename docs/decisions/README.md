# Decision records

One short record per design decision: the context, the options, what was chosen and what it costs.
A record that a later one replaces is marked `superseded`, and it says by which.

| # | Decision |
|---|---|
| [1](0001-toolchain-and-ci.md) | Toolchain and continuous integration |
| [2](0002-commands-in-events-out.md) | Commands in, events out, one command at a time |
| [3](0003-no-clock-in-the-engine.md) | No clock in the engine: time priority is command order |
| [4](0004-market-orders-cancel-the-remainder.md) | Market orders cancel whatever they cannot fill (superseded by 15) |
| [5](0005-modify-uses-fix-total-quantity.md) | Modify takes a new total quantity and keeps priority only when shrinking |
| [6](0006-rejections-are-events.md) | Rejections are events; exceptions are for bugs and I/O |
| [7](0007-write-ahead-journal.md) | The journal is a write-ahead log of commands |
| [8](0008-property-test-design.md) | How the property tests are built |
| [9](0009-order-ids-issued-by-the-engine.md) | Order IDs are issued by the engine |
| [10](0010-prices-are-integer-ticks.md) | Prices are integer ticks, converted at the edge |
| [11](0011-one-engine-per-instrument.md) | One engine and one journal per instrument |
| [12](0012-price-level-structures.md) | Price level structures, pending measurement |
| [13](0013-market-rules-and-price-bands.md) | Market rules per engine, starting with the daily price band |
| [14](0014-day-and-ioc-validity.md) | DAY and IOC validity |
| [15](0015-market-price-protection.md) | Market orders follow NSE's market price protection |
| [16](0016-self-trade-prevention.md) | Self-trade prevention, as NSE's check works |
| [17](0017-fix-session-layer-without-sockets.md) | The FIX session layer has no sockets in it |
| [18](0018-registered-accounts-and-gateway-checks.md) | Accounts are registered before they can trade |
| [19](0019-sessions-persist-their-state.md) | A session's state outlives the process |
| [20](0020-group-commit-before-acknowledgement.md) | Nothing is acknowledged before it is on disk |
| [21](0021-throttles-and-cancel-on-disconnect.md) | Each session has its own limits |
| [22](0022-latency-is-measured-open-loop.md) | Latency is measured open loop, against a baseline |
| [23](0023-the-replay-page.md) | The replay page is generated from a journal |
