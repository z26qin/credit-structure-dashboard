# Two-ticker comparison — design

Date: 2026-10-22 · Status: built (design stated in chat, no approval round)

## 1. Goal

Put two companies side by side on the three questions a credit reader asks first:

1. **Repayment capacity** (还款能力): can each pay its interest and principal from earnings, cash flow and liquidity?
2. **Cash-flow adequacy** (现金流充裕度): how much of what it must fund does its own cash flow cover?
3. **Cash-flow tightness** (现金流紧张程度): how close is it to the edge, and how long does liquidity last without new money?

The companies can differ in size by 100x and in fiscal year. Every comparison is therefore a **ratio** (or a share of
the company's own liquidity or debt), and every time series sits on a **shared calendar axis** at each company's own
period ends, never on fiscal-year labels.

## 2. Approach

No new pipeline stage. A comparison is a pure function of two built `Dashboard`s:

```
build(A), build(B)  (cache when they exist) → compare(A, B) → Comparison → render → out/_compare/A_vs_B/index.html
```

`compare.py` computes every number (so the page, the MCP summary and the tests read the same values); the page's
script only draws. `_compare` and `_vs_` cannot collide with a ticker (tickers are `[A-Z0-9-]`).

## 3. What is reused, what is new, what is dropped

| Single-ticker view | In the comparison |
|---|---|
| Top card: four coverage tests, funding label, bottom line | **Reused** as the verdict card (one column per ticker) and as a dumbbell chart of the four tests |
| Coverage chart (EBITDA ÷ interest, refinanced) | **Reused**, both tickers on one calendar axis; scenario switch limited to scenarios both have |
| Cash to raise each year | **Turned into runway**: liquidity left at each period end, as a share of today's, maturities paid or refinanced |
| Maturity schedule (payout wall) | **Normalized**: cumulative share of today's debt falling due, by date |
| Headline figures (KPIs) | **Reused** as ratios in the measures table; absolute size kept as context rows |
| Market-implied default | Kept as table rows when either side has a CDS or bond yield |
| Capital stack, yield by tranche, debt by what it pays, cash waterfall, instrument table | **Dropped**: about recovery and pricing, not repayment or cash. Each side links to its own dashboard |

New views:

- **Cash-flow adequacy bars**: TTM operating cash flow against next-12-month capex plus scheduled principal, each
  company's needs scaled to 100%.
- **Quarterly trend**: cash interest cover ((CFO + interest paid) ÷ interest paid) and free-cash-flow margin, last eight
  quarters (20-F filers without quarterly XBRL say so).
- **Measures table** with the stronger side marked per measure and a tally per section.

## 4. Measures

"Stronger" is decided per measure by its direction; values within 5% of each other are "even". Self-funding runway
counts as the strongest. A measure missing on either side is not tallied.

| Section | Measure | Better |
|---|---|---|
| Repayment | the four tests (EBITDA, CFO before interest, after capex ÷ next-12m interest; liquidity ÷ next-12m debt service) | higher |
| Repayment | Net debt ÷ TTM EBITDA (n/a when EBITDA ≤ 0) | lower |
| Repayment | Years to repay net debt from free cash flow (n/a when FCF ≤ 0) | lower |
| Adequacy | Cash-flow adequacy: CFO ÷ (capex + next-12m principal) | higher |
| Adequacy | CFO ÷ total debt; FCF ÷ total debt; FCF margin | higher |
| Adequacy | Capex ÷ CFO | lower |
| Tightness | Runway, maturities paid / refinanced (months until projected liquidity < 0) | higher |
| Tightness | Principal due in the next 12 months ÷ debt | lower |
| Tightness | Floating-rate share of debt | lower |
| Context | revenue, EBITDA, debt, cash, liquidity, market cap | not tallied |
| Market | implied 1-year default, spread | lower |

Runway uses the model's own forward rows (consensus capex and CFO when given, else TTM flat), the same inputs as
"cash to raise": liquidity + cumulative (CFO − capex − principal), or − refinancing interest instead of principal
when maturities are refinanced. It is linear within a fiscal year.

## 5. Entry points

- CLI: `creditdash compare A B [--refresh] [--no-llm]`; `creditdash list` lists comparisons separately.
- MCP: `compare_credit(ticker_a, ticker_b)` returns the measures, tallies and verdicts as compact JSON;
  `show_comparison(ticker_a, ticker_b)` shows the page (in chat via the same viewer, else the browser).
- Web: a second form on the index page; `POST /compare` runs both builds as one job; `GET /c/{A}/{B}`.

## 6. Caveats the page states

- Period ends more than 45 days apart: TTM windows differ.
- A side built without the instrument list (`--no-llm` or failed extraction): floating share and the maturity
  profile rest on SEC totals.
- 20-F basis lines and unmodeled-interest warnings of each side are carried over.
