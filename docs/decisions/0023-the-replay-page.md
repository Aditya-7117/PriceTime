# 23. The replay page is generated from a journal

Date: 20 September 2026. Status: accepted.

## Context

A matching engine is hard to judge from a README. The interesting behaviour — a sweep across
several price levels, an order losing its place in the queue, a market order stopped by its
protection band — is obvious in a picture and laborious in prose.

Anything shown to a reader has to be true. A front end that reimplements the matching rules in
JavaScript to draw them is a second implementation that can disagree with the first, and the one on
screen is the one people believe.

## Options

1. **No front end.** Nothing to maintain, nothing to disagree with, nothing to show.
2. **A live web application**, with a server behind it and a websocket feed. Closest to a real
   trading screen, and it drags in a web framework, a build step and a deployment.
3. **A page generated from a journal.** The engine replays its own recorded session, the page is
   written out with the result inside it, and the page only draws.

## Decision

Option 3. `python -m pricetime.viewer <journal>` replays a journal through the real engine and
writes one self-contained HTML file: no build step, no framework, no network, and no order entry,
because it is a window onto a recorded engine rather than a trading screen.

The page draws nothing it works out for itself. The book, the trades, the last traded price and how
far a market order could reach are all computed by the engine during the replay and written into
the page as data.

## Consequences

- What a reader sees is what the engine did, and it cannot drift, because there is no second
  implementation of any rule.
- It opens from a file, works offline, and can be published as a static page.
- It shows a recorded session rather than a live one. A live feed would need the market data the
  project does not have, which is stated as a limitation.
- The page's own arithmetic — turning whole ticks into rupees and grouping the digits — is checked
  against the engine's decimal arithmetic in the test suite, so a price on screen matches a price in
  the journal exactly.
