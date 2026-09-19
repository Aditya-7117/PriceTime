# 10. Prices are integer ticks, converted at the edge

Date: 19 September 2026. Status: accepted.

## Context

The engine compares prices and uses them as dictionary keys. It never does arithmetic on them. A
tick is the smallest price step an instrument allows. The reference venue, NSE, sets tick size per
instrument by price band: 0.01 below ₹250, 0.05 from ₹250 to ₹1,000, and larger steps above that
since April 2025.

## Options

- **Binary floats.** Most decimal fractions cannot be represented exactly, so two equal prices can
  compare unequal. Ruled out.
- **`Decimal`.** Exact, but slower on every comparison, for arithmetic the engine never does.
- **Integer ticks.** Exact and fastest. Something has to know each instrument's tick size.

## Decision

Inside the engine and in the journal, every price is an integer number of ticks. Tick size is
configuration per instrument, not code, with ₹0.05 as the default. At the boundary, a decimal price
string is converted straight to ticks with `Decimal` arithmetic, and a price that is not an exact
multiple of the tick size is rejected there, before it reaches the engine. A float never appears
anywhere in the system.

## Consequences

- The engine has no notion of currency or tick size; it only sees whole numbers.
- One engine serves one instrument for one session, so the unit never changes under it. Comparing
  prices across sessions means converting back through each session's tick size.
