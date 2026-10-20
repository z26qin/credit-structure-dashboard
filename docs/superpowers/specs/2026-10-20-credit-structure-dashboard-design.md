# Credit structure dashboard — design

Date: 2026-10-20 · Status: approved in chat, awaiting spec review
Reference output: `reference/crwv-artifact/` (see `reference/README.md`)

## 1. Goal

Type a ticker, get a credit dashboard for it: how much the company owes, to whom, in what
order, when it comes due, and whether cash flow covers it. The layout and charts follow the
CRWV artifact so every ticker reads the same way.

- Input: one ticker of a company that files 10-K / 10-Q with the SEC.
- Output: one self-contained HTML file, `out/<TICKER>/<period-end>/index.html`, that opens
  offline, plus the `dashboard.json` it was drawn from.
- Two front doors: `creditdash build CRWV` (CLI) and a local web page with a ticker box.

Success criteria:

1. For CRWV (Q2 2026 10-Q, period end 2026-06-30) the dashboard reproduces the artifact's
   10-Q figures: debt principal $35.551B, cash + restricted cash $6.904B, operating lease
   liabilities $16.319B, RPO $103.7B, principal due 2H26 $4.4B / 2027 $6.2B / 2028 $4.4B /
   2029 $2.4B / 2030 $3.2B / thereafter $14.9B, and an instrument list whose June 30
   principal reconciles to $35.551B within 1%.
2. ORCL and CHTR build without code changes and pass or warn (not fail) reconciliation.
3. A rebuild of an already-built filing makes no LLM call and no SEC request beyond the
   submissions index.

## 2. Approach

Hybrid. Code does everything that can be read from SEC structured data, all modeling and
all rendering. An LLM (DeepSeek for now, §3.4) is used once per filing, for one job: turning the debt footnote (and
debt-related 8-Ks filed after the period) into a typed instrument list. Code then checks
that list against the filing before anything is drawn.

Rejected: XBRL only (no instrument list, so no stack, waterfall or detail table) and an
agent that writes each dashboard (output varies run to run and can't be audited).

## 3. Pipeline

```
ticker → resolve → fetch → facts → extract → reconcile → market → model → render
```

Each stage is one module with one public function, JSON-serializable pydantic models in and
out. Stages 1–4 cache to disk keyed by accession number; stages 5–8 are cheap and always rerun.

### 3.1 resolve (`resolve.py`)

- Ticker → CIK via `https://www.sec.gov/files/company_tickers.json` (cached 7 days).
- Latest periodic filing from `data.sec.gov/submissions/CIK##########.json`: the most recent
  10-Q or 10-K (amendments ignored). Its `reportDate` is the **period end**.
- Debt 8-Ks: 8-Ks filed after the period end whose `items` include 1.01 or 2.03.
- A company whose latest periodic form is 20-F, 40-F or 6-K only → `UnsupportedFiler` error.

### 3.2 fetch (`edgar.py`)

- SEC fair-access rules, as in pod-memory: `SEC_USER_AGENT` (must contain an email; error
  otherwise), at most `SEC_MAX_RPS` requests per second (default 5), back off on 429/503.
- Downloads: companyfacts JSON, the periodic filing's XBRL instance (`*_htm.xml`), and each
  debt 8-K's primary document.
- Cache layout: `$CREDITDASH_HOME/cache/<cik>/<accession>/<file>`. Default
  `CREDITDASH_HOME` is `./.creditdash` (gitignored).

### 3.3 facts (`facts.py`, deterministic)

Reads companyfacts and the instance. Every value records `{tag, accession, period}`.

| Field | XBRL source (first one present wins) |
|---|---|
| total debt principal | `DebtInstrumentCarryingAmount`; else `LongTermDebt` + `LongTermDebtCurrent` + `ShortTermBorrowings` |
| cash | `CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents`; else `CashAndCashEquivalentsAtCarryingValue` |
| maturity schedule | `LongTermDebtMaturitiesRepaymentsOfPrincipal*` (rules below) |
| operating / finance lease liability | `OperatingLeaseLiability`, `FinanceLeaseLiability` |
| backlog | `RevenueRemainingPerformanceObligation` |
| revenue, operating income, D&A | `RevenueFromContractWithCustomerExcludingAssessedTax` (else `Revenues`), `OperatingIncomeLoss`, `DepreciationDepletionAndAmortization` |
| interest expense, interest paid | `InterestExpenseDebt` (else `InterestExpense`), `InterestPaidNet` |
| CFO, capex | `NetCashProvidedByUsedInOperatingActivities`, `PaymentsToAcquirePropertyPlantAndEquipment` |
| shares outstanding | `dei:EntityCommonStockSharesOutstanding`, summed across share classes |

Quarterly series cover the last 8 quarters. Quarters are derived from the year-to-date
durations companyfacts reports (Q2 = H1 − Q1, Q4 = FY − 9M). TTM = sum of the last 4.
EBITDA proxy = operating income + D&A; the dashboard labels it "EBITDA (operating income +
D&A)", not "adj. EBITDA".

**Maturity buckets.** Bucket labels are fiscal years. When the filing tags
`...RemainderOfFiscalYear`, that is the first bucket (label e.g. `2H26` for a June quarter,
`Q4 26` for September), and the following tags shift one year:
`...InNextTwelveMonths` = FY+1, `...InYearTwo` = FY+2, `...InYearThree` = FY+3,
`...InYearFour` = FY+4. Without `RemainderOfFiscalYear` (a 10-K), `InNextTwelveMonths` =
FY+1 through `InYearFive` = FY+5. "Thereafter" = `...AfterYearFive` when tagged for this
period, else total principal minus the sum of the dated buckets. This is checked against
CRWV, whose 10-Q text reads "Remaining portion of 2026 $4,413; 2027 6,184; 2028 4,416".

### 3.4 extract (`extract.py`, the one LLM step)

**Input text, located by code:**

- The debt footnote from the instance's iXBRL text block: `us-gaap:LongTermDebtTextBlock`,
  else `DebtDisclosureTextBlock`, else `DebtTextBlock`. CRWV's is ~28k characters
  (~7k tokens). HTML tables are converted to pipe tables so rows and columns survive.
- If no text block is tagged: the 10-Q/10-K HTML section from the first heading matching
  `^(note\s*)?\d*\.?\s*(debt|borrowings|long-term debt|notes payable)` to the next note
  heading. If that also fails, extraction is skipped (see §5).
- For each debt 8-K: the text of its Item 1.01 and Item 2.03 sections. Item 1.01 also covers
  non-debt agreements; the prompt tells the model to ignore those.

Post-period changes can appear both in the footnote's subsequent-events text and in an 8-K.
The prompt states that `principal` is the period-end balance and that each post-period draw
or repayment is recorded once, on its instrument, citing the 8-K when there is one. The CRWV
fixture (DDTL 5.5 and revolver draws, the Sep 2026 converts) is the regression test.

**Call:** DeepSeek, the same way research-relay's `llm.py` does it. `llm.py` is the only module
that talks to a model, with one function: `ask(prompt, system, schema) -> (pydantic object, info)`.

- Plain `httpx` POST to `{LLM_BASE_URL}/chat/completions` (OpenAI-style), bearer `LLM_API_KEY`.
- `.env` (gitignored, `.env.example` committed): `LLM_API_KEY`, `LLM_BASE_URL`
  (default `https://api.deepseek.com`), `LLM_MODEL` (default `deepseek-flash`).
- JSON mode (`response_format: {"type": "json_object"}`) guarantees valid JSON; the
  `Extraction` JSON schema goes in the system prompt to say which JSON. The reply is validated
  with pydantic. A reply that fails validation, a 429/5xx or a network error is retried once
  (backoff 1s); a second failure raises and extraction falls back per §5.
- Timeout 180s (the reply is several thousand tokens). `max_tokens` 16,000, which includes
  hidden reasoning tokens.
- Input over `LLM_MAX_INPUT_TOKENS` (default 100,000, estimated as characters ÷ 4) is not sent
  or truncated; extraction is skipped with that reason.
- The system prompt is fixed text plus the schema; the filing text is the user message.

Keeping the provider behind `ask()` means switching to another provider later (e.g. Claude with
native structured outputs) changes only `llm.py` and `.env`.

**Cache:** one file per request in `$CREDITDASH_HOME/cache/llm/`, named by the sha256 of the
request body (model, prompts, schema, filing text), so a changed prompt or model makes a new
call and an identical one makes none. Each call appends model, tokens in/out, cost, latency,
finish reason and cache hit to `$CREDITDASH_HOME/llm_log.jsonl`. At research-relay's
`deepseek-flash` prices ($0.30 per M input on a cache miss, $1.20 per M output), CRWV's ~7k-token
footnote plus ~5k output tokens is about $0.01 per filing.

**Schema (`Instrument`):**

| Field | Type | Notes |
|---|---|---|
| `name` | str | as the filing names it ("DDTL 4.0 Facility", "9.250% Senior Notes due 2030") |
| `kind` | enum | `term_loan`, `delayed_draw_term_loan`, `revolver`, `senior_notes`, `convertible_notes`, `vendor_financing`, `finance_lease`, `other` |
| `seniority_class` | enum | `asset_level_secured` (SPV / non-recourse), `parent_secured`, `senior_unsecured`, `convertible`, `other` |
| `borrower` | str \| null | entity name when it is a subsidiary or SPV |
| `recourse_to_parent` | bool \| null | |
| `security` | str \| null | collateral in a few words |
| `principal` | float \| null | $ millions outstanding at period end |
| `post_period_change` | float \| null | $ millions drawn (+) or repaid (−) after period end, from an 8-K |
| `commitment` | float \| null | $ millions total facility size |
| `rate_type` | enum | `fixed`, `floating`, `unknown` |
| `fixed_rate_pct` | float \| null | |
| `spread_pct`, `benchmark` | float \| null, str \| null | e.g. 4.00, "SOFR" |
| `effective_rate_pct` | float \| null | only when the filing discloses it |
| `maturity` | str \| null | `YYYY-MM` |
| `amortization` | enum | `bullet`, `amortizing`, `unknown` |
| `currency` | str | ISO code, default USD |
| `source` | str | accession number the fields came from |
| `quotes` | list[str] | 1–3 verbatim snippets (≤ 300 chars each) that support principal, rate and maturity |

`Extraction` = `{period_end, unit_note, instruments: list[Instrument]}`.

### 3.5 reconcile (`reconcile.py`, deterministic)

- **Quote check:** every quote must be a substring of its source text after normalizing
  whitespace, curly quotes and dashes. An instrument with any failing quote is kept but marked
  `unverified`.
- **Total check:** |Σ instrument `principal` − facts total principal| ÷ total:
  ≤ 1% pass, ≤ 5% warn, > 5% fail. The gap is carried as an "Unallocated" instrument
  (seniority `other`, no rate) so charts still sum to the reported total.
- **Maturity check:** instruments bucketed by maturity year vs the reported schedule. This is
  reported but never sets fail, because amortization shifts principal between years.
- Output: `Reconciliation{status, total_gap, unverified[], maturity_diff[]}`. The dashboard
  header shows a badge; the method section lists every finding.

### 3.6 market (`market.py`)

- SOFR: NY Fed `markets.newyorkfed.org/api/rates/secured/sofr/last/1.json` (checked: returns
  3.87% for 2026-10-19).
- Share price: Yahoo chart endpoint (`query1.finance.yahoo.com/v8/finance/chart/<T>`,
  unofficial; checked: works). Market cap = price × shares outstanding.
- `overrides/<TICKER>.yaml` (optional, user-written) can set or replace: `price`,
  `consensus` (per fiscal year: revenue, ebitda, capex, cfo), `ratings` and `bond_yields` (yield-to-worst %, as quoted by a bond data site)
  (by instrument name), `ebitda_label` (e.g. "adj. EBITDA" when consensus is adjusted).
- Any source that fails → the value is `null` with a reason; the build continues.

### 3.7 model (`model.py`, deterministic)

Settings in `config/model.yaml` (stress haircut, horizon); defaults below.

- **Balances:** pro forma = `principal` + `post_period_change`.
- **Principal schedule per instrument:** bullets repay at maturity. Amortizing loans start
  straight-line from the first bucket to maturity, then per year are scaled so bullets +
  amortizing equal the reported bucket (iterative proportional fitting, ≤ 50 rounds,
  keeping each instrument's total equal to its balance). Remaining fit error goes into the
  reconciliation output. Post-period additions are not in the reported schedule and keep their
  own straight-line or bullet schedule.
- **Interest:** coupon × average balance in the period. Floating = current SOFR + spread, held
  flat. Unknown rate → the effective rate if disclosed, else excluded and flagged.
- **Horizon:** first bucket through the latest maturity, capped at 10 years.
- **EBITDA scenarios:**
  - consensus, when overrides provide it, held flat after its last year;
  - TTM, held flat;
  - run-rate = latest quarter × 4, held flat (for fast growers TTM understates);
  - stress = the base (consensus if present, else TTM) × (1 − 30%) from FY+1.
- **Coverage (DSCR)** = scenario EBITDA ÷ (interest + scheduled principal), per year.
- **Cash to raise** = capex + scheduled principal − CFO, floored at 0. Uses consensus capex
  and CFO when present, else TTM, flat.
- **Capital stack:** classes in seniority order, then equity at market cap.
  Attachment point of a class = share of (total debt + market cap) that is junior to it.
  Without market cap, the stack is debt only and the chart says so.
- **Cash waterfall** for a chosen year: EBITDA pays each class's interest + principal in
  seniority order; what is left after the last class is the residual; a shortfall is shown
  red at the class where cash runs out.

### 3.8 render (`render.py`, `templates/dashboard.html.j2`, `static/`)

One Jinja2 template; `dashboard.json` and d3 v7 (vendored file) are inlined so the HTML is a
single offline file. Light and dark themes. Sections:

1. Header: name, ticker, period end, filing link, reconciliation badge; KPIs: total debt, cash,
   net debt, net debt ÷ TTM EBITDA, TTM EBITDA ÷ TTM interest, operating lease liabilities,
   backlog, weighted average rate (disclosed effective rates, else computed coupon).
2. Capital structure, senior to junior (stacked bar with attachment points).
3. Yield by tranche and maturity (bubbles: x maturity, y yield, size principal). Yield =
   override yield when given, else all-in coupon, labeled which.
4. Debt by what it pays (principal × rate, highest rate first).
5. Payout wall vs EBITDA (stacked by class per year, scenario selector, uncovered part red).
6. Coverage by scenario (DSCR lines, 1.0x reference).
7. Cash to raise per year.
8. Cash waterfall (year selector).
9. Instrument table (sortable; unverified rows marked).
10. Sources and method: every input's provenance, the reconciliation findings, model settings.

### 3.9 Interfaces

- CLI (`creditdash`): `build <TICKER> [--refresh] [--no-llm]`, `serve [--port 8765]`,
  `list`. `--refresh` ignores caches; `--no-llm` builds the XBRL-only view.
- Web (FastAPI, local only, binds 127.0.0.1): `GET /` lists built dashboards and has a ticker
  form; `POST /build` starts a build in a background thread and returns a job id;
  `GET /jobs/{id}` returns stage progress; `GET /d/{ticker}/{period}` serves the HTML.
  One build per ticker at a time.

## 4. Package layout

```
creditdash/
  cli.py  config.py  errors.py  models.py
  resolve.py  edgar.py  facts.py  extract.py  reconcile.py  market.py  model.py  render.py
  llm.py             # the only module that calls a model (DeepSeek)
  pipeline.py        # runs the stages, reports progress, writes out/
  web.py             # FastAPI app
  prompts/extract_v1.md
  templates/dashboard.html.j2
  static/d3.v7.min.js  dashboard.js  dashboard.css
config/model.yaml
overrides/           # <TICKER>.yaml, user-written, optional
tests/  fixtures/crwv-2026q2/
```

Python ≥ 3.12, uv, hatchling. Dependencies: fastapi, uvicorn, httpx, pydantic,
jinja2, pyyaml, python-dotenv, beautifulsoup4 + lxml (text block and 8-K parsing). Dev: pytest.

## 5. Failure handling

| Situation | Result |
|---|---|
| unknown ticker | error, exit code 2, no output |
| foreign filer (20-F / 40-F) | `UnsupportedFiler` error naming the form |
| SEC unreachable / 403 | error naming `SEC_USER_AGENT` when it is a 403 |
| no debt footnote found, no API credentials, or LLM call fails | XBRL-only dashboard: KPIs, maturity wall, coverage and cash-to-raise on totals; sections 2, 3, 4, 8, 9 show "instrument detail unavailable: <reason>" |
| reply still fails schema validation after one retry, or input over `LLM_MAX_INPUT_TOKENS` | same as above, reason recorded |
| reconciliation fail | dashboard built, red badge, "Unallocated" gap visible |
| market data source down | affected values null with reason; capital stack falls back to debt only |

## 6. Testing

- Fixtures: CRWV Q2 2026 companyfacts, instance XML and the debt text block, plus one recorded
  `Extraction` response, all saved under `tests/fixtures/crwv-2026q2/`. Tests are offline.
- `facts`: golden values from §1 criterion 1, including the shifted maturity buckets.
- `extract`: footnote location and table conversion on the fixture; schema round-trip of the
  recorded response; the LLM call itself is not run in tests.
- `llm`: request body (JSON mode, schema in the system prompt), retry on an invalid reply then
  success, raise after two failures, cache hit makes no request, oversize input is refused. All
  against a mocked `httpx` transport.
- `reconcile`: quote normalization; pass / warn / fail thresholds; Unallocated row.
- `model`: bullet and amortizing schedules, fitting keeps instrument totals, floating interest,
  DSCR and cash-to-raise arithmetic, attachment points.
- `render`: builds from the fixture; output contains all 10 section ids and parses as HTML;
  embedded JSON validates against the model.
- Manual acceptance: live builds of CRWV, ORCL and CHTR (§1 criteria 2–3).

## 7. Out of scope (v1)

The CRWV business-model / unit-economics section, LLM-written narrative, non-SEC filers,
TRACE bond prices, paid data feeds, ratings lookup (overrides only), covenant analysis,
multi-user hosting and authentication.
