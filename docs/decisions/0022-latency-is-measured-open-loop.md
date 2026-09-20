# 22. Latency is measured open loop, against a baseline

Date: 20 September 2026. Status: accepted.

## Context

The easy way to benchmark a server is a loop: send an order, wait for the reply, time it, repeat.
It is also the wrong way. If the server stalls, the loop stalls with it and simply sends fewer
orders, so the stall never appears in the results. The percentiles come out flattering and the
worst moments are the ones least likely to be measured. This is coordinated omission, and it is the
usual reason a published p99 is fiction.

## Decision

The benchmark fixes the schedule before the run starts and never lets the system under test slow it
down. Every latency is measured from the time an order was *due*, not from the time it was actually
sent.

Two numbers are reported for each run: service time, the work itself, and response time, which
includes the time an order spent waiting for its turn. When the system keeps up they are nearly
equal, and when it does not, the difference is the queue.

Also fixed in the method: the first two seconds are discarded as warm-up, every sample is kept so
the percentiles are exact rather than estimated from buckets, and a baseline is measured with the
same harness — a bare asyncio echo over the same kind of socket — so a round-trip number can be
read against the floor of the machine it ran on.

## Consequences

- The numbers are worse than a closed-loop benchmark would produce, and they are the ones a client
  would actually feel.
- The baseline makes the honest claim possible: most of a FIX round trip on this machine is
  asyncio and the operating system, not the exchange.
- Every published figure states its hardware, its load, its warm-up and its run length
  ([docs/benchmarks.md](../benchmarks.md)), so it can be argued with.
- The engine is also measured twice, once with the garbage collector running and once with it
  paused, because the difference is the honest answer to why the tail is so far from the median.
