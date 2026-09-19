# 15. Market orders follow NSE's market price protection

Date: 19 September 2026. Status: accepted. Supersedes [decision 4](0004-market-orders-cancel-the-remainder.md).

## Context

Decision 4 cancelled whatever a market order could not fill. That is simple, but it gives no
protection: a market order into a thin book trades at whatever prices happen to be there. The
reference venue, NSE, has published exactly how it handles market orders, in circular 155/2022
(NSE/CMTR/54851, 16 December 2022, "Pre-trade risk controls: Market Price Protection").

## Decision

Market orders follow that circular:

1. A market order in an instrument that has not traded yet this session is rejected
   (`NO_LAST_TRADE_PRICE`). A session may open with a last traded price, such as a pre-open
   auction's equilibrium price.
2. It may trade only up to X% above (buy) or below (sell) the last traded price at the moment it
   arrives. X is a setting in basis points, and the band never narrows below a minimum number of
   ticks. The band is rounded down so it never exceeds X%.
3. If orders remain beyond the band on the other side, the remainder is cancelled
   (`PRICE_PROTECTION`).
4. Otherwise the other side is empty. An IOC order's remainder is cancelled (`UNFILLED_IOC`). A DAY
   order's remainder rests as a limit order at the best price on its own side, or at the last traded
   price if its own side is empty too, and the engine reports `MarketOrderConverted`.

The engine tracks the last traded price as part of its state, and the snapshot and its digest
include it.

## Consequences

- A market order can never trade more than X% away from the last trade, however thin the book.
- X is configuration, not a constant, because the circular leaves its value to a separate notice.
  The value in any test or example is illustrative, not NSE's.
- Every share is still accounted for: filled, cancelled, or resting as a converted limit order.
- Not modelled from the same circular: stop-loss market orders, and modifying an order into a
  market order.
