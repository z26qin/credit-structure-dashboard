# creditdash

Ticker in, credit-structure dashboard out. For a company that files 10-Ks and 10-Qs (or a 20-F in US GAAP) with the SEC,
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
| `LLM_API_KEY` | DeepSeek key, for the three model reads (see "Where the model is used"); without it you get SEC totals only, no 6-K figures and no market read |
| `LLM_BASE_URL`, `LLM_MODEL` | Default `https://api.deepseek.com`, `deepseek-flash` |
| `LLM_REASONING_EFFORT` | Default `low` (see "Cost and speed") |

## Use

```bash
uv run creditdash build CRWV            # writes out/CRWV/<period end>/index.html
uv run creditdash build CRWV --no-llm   # no model calls: SEC totals only, no 6-K figures, no market read
uv run creditdash build CRWV --refresh  # ignore cached SEC files and cached model answers
uv run creditdash serve --port 8777     # local page with a ticker box (binds 127.0.0.1)
uv run creditdash list
```

Optional hand-written inputs (consensus, bond yields, 5-year CDS, ratings, share price) go in
`overrides/<TICKER>.yaml`; see `overrides/README.md`. Without a bond yield or CDS the top card shows the
cash-flow side only.

## How it works

```
ticker → resolve → fetch → facts → extract → reconcile → market → model → render
```

- **resolve / fetch**: SEC EDGAR — the latest 10-Q or 10-K, the latest 10-K alongside a 10-Q, and debt
  8-Ks (items 1.01 / 2.03) filed since the period end. Throttled to 5 requests/s, cached under `.creditdash/`.
- **facts**: XBRL totals (debt, cash, leases, backlog), the maturity schedule (the 10-K's when the 10-Q has
  none), and eight quarters of revenue, EBITDA (operating income + D&A), interest, cash flow and capex.
- **extract**: code finds the debt note (the 10-Q's and the 10-K's) and the 8-K items; DeepSeek returns a
  typed instrument list with verbatim quotes.
- **reconcile**: every quote must appear in the filing text sent; instruments must add up to the reported
  debt (≤1% pass, ≤5% warn, else fail). The gap is drawn as "Unallocated", never filled in.
- **model**: monthly debt service per instrument, fitted to the reported maturity schedule; EBITDA
  scenarios (consensus if given, TTM, run-rate, stress −30%); coverage, cash to raise, capital stack with
  attachment points, and the cash waterfall. Maturities are assumed refinanced at today's average rate
  (`refinancing_rate_pct` in `config/model.yaml` overrides it), so their interest keeps running; capex
  funding stays in "cash to raise".
- **top card** (`headline.py`, `creditrisk.py`, run in the model stage): four tests of the next 12 months'
  interest (EBITDA, operating cash flow before interest, the same after capex, and liquidity, meaning cash
  plus undrawn revolvers, against interest and principal), each covered (≥2.0x; liquidity ≥1.5x), thin
  (≥1.0x) or not covered, and a bottom line written from those statuses. Beside them, the default
  probability a 5-year CDS or the worst bond's yield over SOFR implies, and the model's read of price
  against cash flow.
- **render**: one template, data and d3 inlined. Every number names its source in "Sources and method".

## Where the model is used

Three steps ask DeepSeek, and code checks every answer before it reaches the page:

| Step | Prompt | What code checks |
|---|---|---|
| Debt note → instrument list | `extract_v6.md` | each quote is in the filing text; instruments add up to the reported debt |
| 6-K statements (20-F filers only) | `interim_v2.md` | each figure's row is in the 6-K text and shows the number |
| Market vs cash flow read | `risk_v3.md` | it writes no numbers: figures are `{key}` placeholders that code fills in, and a draft with its own numbers or unknown keys is sent back once, then left out |

Each `dashboard.json` records the prompts used (`prompt_version`, `interim_prompt_version`,
`risk_prompt_version` in `settings`); the page's "Sources and method" names the debt-note and market-read
prompts.

## 20-F filers (e.g. NBIS)

A foreign private issuer files a 20-F once a year and furnishes quarterly results on 6-K, without XBRL.
creditdash takes the 20-F's full year from XBRL, then looks through the 6-Ks filed after it for an exhibit
with interim financial statements. If it finds one, the model reads the three statements and the debt note
(prompt `creditdash/prompts/interim_v2.md`); every figure must sit in a row of the 6-K text that shows the
number, or it is dropped. The dashboard is then dated at the interim period end: balances and total debt
(principal per the debt note) from the 6-K, last-twelve-month figures as full year + year to date − the
prior year to date, and the debt list read from the 6-K's debt note with the 20-F for detail. Later 6-Ks
that mention debt (offerings, indentures, credit agreements) are read as post-period changes. The banner at
the top of the page says which figures come from which filing. With `--no-llm`, or without such a 6-K,
the figures stay as of the 20-F.

## Cost and speed

`deepseek-flash` reasons before it answers, and the reasoning counts against `max_tokens`. At default
effort it used the whole 16k budget on CRWV's debt note without answering, so requests use
`reasoning_effort: low` with 32k tokens and retry once with thinking off if a reply is still cut off.
A new filing takes about 30–80 s and $0.01–0.03; rebuilds of the same filing are served from cache.

## Tests

```bash
uv run pytest -q
```

`uv run python scripts/build_pages.py` rebuilds `docs/index.html`, the GitHub Pages copy of this README
(styled by `docs/page.html`); run it after editing the README.

Offline: SEC, NY Fed, Yahoo and DeepSeek are mocked. `tests/fixtures/crwv-2026q2/` holds CoreWeave's
Q2 2026 filings and an accepted extraction, checked as a golden test.

## Limits

- 10-K/10-Q filers, and 20-F filers that report in US GAAP. 40-F filers and IFRS 20-F filers are refused
  with an explanation.
- Free data only: SOFR from the NY Fed, share price from Yahoo's unofficial endpoint. Consensus, bond
  yields and ratings come from `overrides/`.
- Quarterly filings often list debt only in categories; the 10-K fills in instrument detail, so balances
  may be as of the 10-K date (the reconciliation badge shows the gap).
- Debt with no maturity (category totals, the unallocated gap) follows whatever the reported schedule
  leaves after dated instruments; the method section says so.
- The model's reading varies run to run; the cache keeps a filing's answer stable once accepted. That
  includes the market verdict: new figures or a new prompt can change it.
