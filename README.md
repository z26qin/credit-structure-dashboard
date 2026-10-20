# creditdash

Ticker in, credit-structure dashboard out. For a company that files 10-Ks and 10-Qs with the SEC,
`creditdash` reads its latest filings and builds one offline HTML page for a credit investor: who the
company owes, in what order, when it comes due, and whether cash flow covers it. The layout follows the
CRWV dashboard in `reference/crwv-artifact/`.

## Setup

```bash
uv sync
cp .env.example .env   # then fill it in
```

`.env` (never committed):

| Variable | What it is |
|---|---|
| `SEC_USER_AGENT` | Required by the SEC: `Your Name you@example.com` |
| `LLM_API_KEY` | DeepSeek key, for the instrument extraction (without it you get the SEC-totals view) |
| `LLM_BASE_URL`, `LLM_MODEL` | Default `https://api.deepseek.com`, `deepseek-flash` |
| `LLM_REASONING_EFFORT` | Default `low` (see "Cost and speed") |

## Use

```bash
uv run creditdash build CRWV            # writes out/CRWV/<period end>/index.html
uv run creditdash build CRWV --no-llm   # SEC totals only, no model call
uv run creditdash build CRWV --refresh  # ignore cached SEC files and the cached model answer
uv run creditdash serve --port 8777     # local page with a ticker box (binds 127.0.0.1)
uv run creditdash list
```

Optional hand-written inputs (consensus, bond yields, ratings, share price) go in
`overrides/<TICKER>.yaml`; see `overrides/README.md`.

## How it works

```
ticker → resolve → fetch → facts → extract → reconcile → market → model → render
```

- **resolve / fetch**: SEC EDGAR — the latest 10-Q or 10-K, the latest 10-K alongside a 10-Q, and debt
  8-Ks (items 1.01 / 2.03) filed since the period end. Throttled to 5 requests/s, cached under `.creditdash/`.
- **facts**: XBRL totals (debt, cash, leases, backlog), the maturity schedule (the 10-K's when the 10-Q has
  none), and eight quarters of revenue, EBITDA (operating income + D&A), interest, cash flow and capex.
- **extract**: the only model call. Code finds the debt note (the 10-Q's and the 10-K's) and the 8-K items;
  DeepSeek returns a typed instrument list with verbatim quotes. Prompt: `creditdash/prompts/extract_v4.md`.
- **reconcile**: every quote must appear in the filing text sent; instruments must add up to the reported
  debt (≤1% pass, ≤5% warn, else fail). The gap is drawn as "Unallocated", never filled in.
- **model**: monthly debt service per instrument, fitted to the reported maturity schedule; EBITDA
  scenarios (consensus if given, TTM, run-rate, stress −30%); coverage, cash to raise, capital stack with
  attachment points, and the cash waterfall.
- **render**: one template, data and d3 inlined. Every number names its source in "Sources and method".

## Cost and speed

`deepseek-flash` reasons before it answers, and the reasoning counts against `max_tokens`. At default
effort it used the whole 16k budget on CRWV's debt note without answering, so requests use
`reasoning_effort: low` with 32k tokens and retry once with thinking off if a reply is still cut off.
A new filing takes about 30–80 s and $0.01–0.03; rebuilds of the same filing are served from cache.

## Tests

```bash
uv run pytest -q
```

Offline: SEC, NY Fed, Yahoo and DeepSeek are mocked. `tests/fixtures/crwv-2026q2/` holds CoreWeave's
Q2 2026 filings and an accepted extraction, checked as a golden test.

## Limits

- US filers of 10-K/10-Q only (no 20-F/40-F).
- Free data only: SOFR from the NY Fed, share price from Yahoo's unofficial endpoint. Consensus, bond
  yields and ratings come from `overrides/`.
- Quarterly filings often list debt only in categories; the 10-K fills in instrument detail, so balances
  may be as of the 10-K date (the reconciliation badge shows the gap).
- Debt with no maturity (category totals, the unallocated gap) follows whatever the reported schedule
  leaves after dated instruments; the method section says so.
- The model's reading varies run to run; the cache keeps a filing's answer stable once accepted.
