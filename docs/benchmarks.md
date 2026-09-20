# Latency, and how it was measured

Every number here came from one run of `python -m pricetime.bench`, on the machine and settings
below. Nothing is scaled, rounded in the project's favour, or carried over from an earlier run.

## The method

**Open loop.** Orders go out on a schedule fixed before the run starts, and every latency is
counted from the moment an order was *due*, not from when it was actually sent. This is what
avoids coordinated omission. In a closed loop, where each order waits for the previous reply, a
slow engine quietly slows the load generator down with it: the stall never lands in the numbers,
and the percentiles come out flattering. Here the schedule ignores how slow the engine is, so one
stall is counted against every order that was waiting during it, which is what a client feels.

Two numbers are reported for each run:

- **Service time**: the work itself, from the moment the order was picked up to the moment it was
  done.
- **Response time**: from the moment the order was due until it was done. When the system keeps up,
  the two are nearly equal. When it does not, the gap is the queue.

**Warm-up is discarded.** The first two seconds pay for an empty book, cold caches and a young
interpreter. Only the steady state is measured.

**Exact percentiles.** Every sample is kept and sorted, so p50, p99 and p99.9 are exact rather than
estimated from buckets.

**A baseline is measured too.** A round-trip number means nothing on its own, so the same harness
measures an empty asyncio echo over the same kind of loopback socket. Whatever the exchange costs,
it costs on top of that.

## Where it was measured

| | |
|---|---|
| Machine | Apple M5 Pro, 24 GB, macOS (Darwin 25.6.0) |
| Python | CPython 3.12.13 |
| Commit | `e45352b` |
| Measured | 20 September 2026 |
| Load | 20,000 orders per second at the engine; 1,000 per second over FIX |
| Steady state | 5 seconds after 2 seconds of warm-up |
| Order mix | 70% limit, 5% market, 15% cancel, 10% modify, across 4 clients, prices around one mid |

Reproduce with:

```sh
python -m pricetime.bench --rate 20000 --fix-rate 1000 --seconds 5 --warmup 2
```

## The engine

Microseconds, 100,000 samples per row.

| Measurement | p50 | p99 | p99.9 | max |
|---|---|---|---|---|
| Service time, collector on | 1.38 | 4.54 | 7.83 | 8,818 |
| Response time, collector on | 1.42 | 5.00 | 4,028 | 8,818 |
| Service time, collector paused | 1.38 | 4.33 | 6.83 | 149 |
| Response time, collector paused | 1.42 | 4.71 | 1,823 | 6,620 |

**The middle of the distribution is the engine; the tail is Python.** Applying one command takes
about 1.4 microseconds at the median and under 5 at the 99th percentile. The worst case with the
garbage collector running is 8.8 milliseconds, six thousand times the median. Pausing the collector
for the steady state drops the worst service time to 149 microseconds, which is the measurement,
not the argument: the tail belongs to the runtime, not to the matching.

Nothing should run with the collector switched off for long. The pair of runs is there to show
where the tail comes from, and it is the honest answer to "why is your p99.9 so far from your p50?"

## The FIX round trip

From the moment an order is due at the client to the moment its execution report is back, over a
loopback socket. Microseconds, 5,000 samples per row.

| Measurement | p50 | p99 | p99.9 | max |
|---|---|---|---|---|
| Baseline: bare asyncio echo, service | 116 | 182 | 283 | 454 |
| Round trip, forced to disk, service | 524 | 1,286 | 4,789 | 23,591 |
| Round trip, no disk sync, service | 520 | 1,557 | 5,072 | 20,469 |

**Most of a round trip is not the exchange.** An empty echo over the same socket costs 116
microseconds at the median on this machine, and that is with no parsing, no book, no journal and no
reply to build. The exchange adds roughly 400 microseconds on top: reading and checking the
message, the gateway's own checks, the engine, writing the journal, building the execution report
and writing it to the session store, and the event-loop hops between them. Measured separately, the
work itself accounts for about 50 microseconds of that; the rest is Python's asyncio machinery and
the operating system.

**Forcing every batch to disk is nearly free at this rate, and the tail says why.** The medians
differ by four microseconds because a batch of orders shares one disk sync, which is the whole point
of group commit. The difference shows up further out, where a sync that lands badly costs
milliseconds.

## What these numbers are not

- **Not a comparison with a production exchange.** A matching engine written in C++ on tuned
  hardware works in tens of nanoseconds. This is Python on a laptop, and the project weighs
  correctness and determinism above speed on purpose.
- **Not a throughput claim.** The engine was driven at a fixed 20,000 orders per second, which it
  kept up with; the highest rate it could sustain was not measured.
- **Not multi-instrument.** One engine, one instrument, one client session.
- **Not tuned.** No process pinning, no priority changes, no interpreter flags, and the laptop was
  otherwise idle but not quiesced.

## What would make it faster

In order of expected effect:

1. **The C++ port of the hot path**, which is what February is for.
2. **A binary journal format.** JSON Lines is readable and slow; the format has a version header so
   a binary one can arrive without breaking old journals.
3. **Fewer event-loop hops per message** in the gateway, which the round-trip figures above suggest
   is worth more than anything inside the engine.
4. **`OrderedDict` instead of the intrusive queue**, if measurement supports it: the intrusive list
   is a C++ idea, and in CPython a structure written in C may well win.
