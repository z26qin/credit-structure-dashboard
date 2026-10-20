# Reference: CRWV debt dashboard (Claude artifact)

Source: https://claude.ai/artifact/DRSsSQXysBxAEFm4tQ8Ytp ("CRWV debt waterfall & maturity wall"),
copied 2026-10-20. This is the target output the app should reproduce for any ticker.

- `crwv-artifact/index.html` — the page. It runs inside the Claude dashboard runtime
  (`dash.data`, `dash.onData`, d3 global), so it will not open standalone. Treat it as a
  design and chart reference.
- `crwv-artifact/datasets/*.json` — each dataset's title, description and provenance note.
- `crwv-artifact/data/*.json` — the rows each chart reads.

## What each section needs, and where it can come from

| Dashboard section | Data it reads | Source for any ticker |
|---|---|---|
| Header KPIs (debt, cash, leases, backlog, eff. rate) | `market` | SEC XBRL (deterministic) + market cap |
| Capital structure, senior to junior (tranche stack, attachment points) | `instruments`, market cap | Instrument list needs the debt footnote (LLM) |
| Yield by tranche and maturity (bubbles) | `instruments` yields/prices | Bond prices: market data (not in SEC) |
| Debt stack by what they pay (principal × effective rate) | `instruments` | Debt footnote (LLM); partial in XBRL |
| Payout wall vs adj. EBITDA | `debt_service`, `scenarios_model`, `consensus` | Modeled from instruments + estimates |
| Debt-service coverage by scenario | same | Modeled (deterministic code) |
| Cash to raise each year (capex + maturities − CFO) | `consensus`, `reported_maturities` | Estimates (market data) + XBRL maturities |
| Cash waterfall (SPV lenders first, then parent) | `instruments`, `consensus` | Needs seniority/recourse from footnote (LLM) |
| Business model (Chinese section, GS single-project model) | `biz_model` | Bespoke analysis from a sell-side chart; not reproducible by a pipeline |
| Tranche detail table | `instruments` | Debt footnote (LLM) + ratings/prices (market data) |

## What SEC XBRL gives for CRWV (probed 2026-10-20)

`data.sec.gov/api/xbrl/companyfacts` (no key, free) returns clean totals that match the artifact:

- `DebtInstrumentCarryingAmount` 35.551B, cash + restricted 6.904B, `OperatingLeaseLiability` 16.319B,
  `RevenueRemainingPerformanceObligation` 103.7B — all as of 2026-06-30.
- Maturity schedule (`LongTermDebtMaturitiesRepaymentsOfPrincipal*`), lease payment schedule,
  interest expense, interest paid, CFO, capex, revenue, D&A history.
- Caveat: tag semantics drift. The 10-Q's "next twelve months" tag (6.184B) is what the artifact
  shows as 2027; the year-bucket mapping needs per-filing logic, not a fixed lookup.

Per-instrument data is weak in XBRL. The Q2 2026 10-Q instance has 78 contexts on
`DebtInstrumentAxis`, but only ~10 members carry usable single-dimension facts, mostly stated
coupons; carrying amounts per facility, maturity, borrower SPV, lien and recourse live in the
footnote's HTML tables and prose. Ratings, bond prices/yields, consensus estimates and SOFR are
not in SEC filings at all.
