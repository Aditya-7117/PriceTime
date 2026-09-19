# 12. Price level structures, pending measurement

Date: 19 September 2026. Status: accepted.

## Context

Each side of the book needs its best price at once, and must add and drop levels as they appear
and empty. Each level needs a first-in, first-out queue that supports removal from the middle,
because that is what cancel does.

## Decision

- **Levels on a side** sit in a sorted Python list of keys, searched with `bisect`, arranged so the
  best price is last. Reading the best level is O(1). Adding or dropping a level costs a binary
  search plus a shift of the keys after it; the shift is empty at the best price and short near the
  top of the book, where most levels come and go.
- **The queue in a level** is an intrusive doubly linked list: each order carries its own `prev` and
  `next`. An order found through the ID index is unlinked in O(1).

Alternatives considered: a heap with lazy deletion, `sortedcontainers.SortedDict` (the project's
first runtime dependency), an array indexed by tick within the price band, and
`collections.OrderedDict` per level.

## Consequences

In C++ an intrusive list avoids a node allocation per order and keeps data together in memory.
Neither benefit exists in CPython, where every object is its own heap allocation; what remains is
O(1) unlink and a one-to-one mapping onto a C++ port. `OrderedDict`, a hash table and linked list
written in C, may be faster in Python. The latency benchmark measures both structures against
their alternatives, and the result is published either way.
