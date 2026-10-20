# Credit Structure Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `creditdash build <TICKER>` turns a US SEC filer's latest 10-Q/10-K into a single offline HTML credit dashboard (debt stack, maturity wall, coverage, cash to raise, waterfall, instrument table) laid out like the CRWV reference artifact.

**Architecture:** Eight pipeline stages (resolve → fetch → facts → extract → reconcile → market → model → render), each a module with one public function and pydantic models in/out. Everything is deterministic except `extract`, which sends the debt footnote to DeepSeek through `llm.py` (the only module that calls a model) and is checked by `reconcile` before anything is drawn. A FastAPI page and a CLI both call `pipeline.build()`.

**Tech Stack:** Python ≥ 3.12, uv, hatchling, httpx, pydantic v2, jinja2, pyyaml, python-dotenv, beautifulsoup4 + lxml, FastAPI + uvicorn, d3 v7 (vendored), pytest.

**Spec:** `docs/superpowers/specs/2026-10-20-credit-structure-dashboard-design.md`. Reference output: `reference/crwv-artifact/` (page + data), described in `reference/README.md`.

## Global Constraints

- Python `>=3.12`; runtime deps exactly: fastapi, uvicorn, httpx, pydantic, jinja2, pyyaml, python-dotenv, beautifulsoup4, lxml. Dev: pytest. No `anthropic`, no `openai`.
- All money in models is **$ millions as float**. The page displays $B.
- SEC: every request sends `SEC_USER_AGENT` (must contain `@`, else error); at most `SEC_MAX_RPS` (default 5) requests/s; back off and retry on 429/503.
- `CREDITDASH_HOME` default `./.creditdash` (cache, `llm_log.jsonl`); output under `CREDITDASH_OUT` default `./out`. Both gitignored, as is `.env`.
- LLM: only `creditdash/llm.py` calls a model. `.env`: `LLM_API_KEY`, `LLM_BASE_URL` (default `https://api.deepseek.com`), `LLM_MODEL` (default `deepseek-flash`), `LLM_MAX_INPUT_TOKENS` (default 100000, estimated as chars ÷ 4). JSON mode, schema in system prompt, timeout 180 s, `max_tokens` 16000, one retry.
- Reconciliation: |gap| ÷ reported ≤ 1% pass, ≤ 5% warn, > 5% fail.
- EBITDA is labeled "EBITDA (operating income + D&A)" unless overrides set `ebitda_label`.
- The output HTML is one offline file: no `<script src>` or `<link href>` to anything outside it.
- Web server binds `127.0.0.1` only.
- Tests never touch the network: SEC, NY Fed, Yahoo and DeepSeek are served by `httpx.MockTransport`.

## Review Focus

1. **Instrument with null `principal` or `maturity`** (LLM leaves revolver maturity blank) → model excludes it from the schedule, lists it in `ModelOutput.excluded`, never crashes. Test in Task 11.
2. **Non-calendar fiscal year** (ORCL, FYE May 31) → bucket labels `FY27 Q2–Q4`, `FY28`…, quarters derived from FY-start YTD facts. Test in Task 5.
3. **Extraction in the wrong unit** (thousands or billions while the schema says millions) → reconcile detects a ~1000× ratio, rescales, records a note, and judges the gap after rescaling. Test in Task 9.
4. **Debt note not tagged as a text block, heading worded differently** ("Note 9. Borrowings and Credit Facilities") → HTML fallback still finds it. Test in Task 6.
5. **Instrument name or quote containing `</script>` or HTML** → rendered as text, embedded JSON cannot close its `<script>` tag. Test in Task 12.

---

### Task 1: Scaffold, config, errors, fixtures

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.env.example`, `creditdash/__init__.py`, `creditdash/config.py`, `creditdash/errors.py`, `config/model.yaml`, `tests/__init__.py`, `tests/test_config.py`
- Create fixtures: `tests/fixtures/crwv-2026q2/{company_tickers.json, submissions.json, companyfacts.json, instance.xml, 8k-20260807.htm, 8k-20260917.htm}`

**Interfaces:**
- Produces: `config.Settings` (frozen dataclass) with `sec_user_agent: str`, `sec_max_rps: float`, `home: Path`, `out: Path`, `llm_api_key: str | None`, `llm_base_url: str`, `llm_model: str`, `llm_max_input_tokens: int`; `Settings.from_env() -> Settings` (loads `.env` with `override=False`); `Settings.user_agent() -> str` (raises `ConfigError` when empty or no `@`).
- Produces: `config.ModelSettings(stress_haircut: float = 0.30, horizon_years: int = 10, ipf_max_rounds: int = 50)`, `load_model_settings(path=Path("config/model.yaml")) -> ModelSettings`.
- Produces: `errors.py`: `CreditdashError(Exception)` with class attr `exit_code`; subclasses `ConfigError`(1), `UnknownTicker`(2), `UnsupportedFiler`(3), `SecError`(4), `LlmError`(5).

- [ ] **Step 1:** `git init`; write `pyproject.toml` (name `creditdash`, script `creditdash = "creditdash.cli:main"`, hatchling, `[tool.pytest.ini_options] testpaths=["tests"]`), `.gitignore` (`.env`, `.creditdash/`, `out/`, `.venv/`, `__pycache__/`), `.env.example` (`SEC_USER_AGENT=Your Name you@example.com`, `SEC_MAX_RPS=5`, `LLM_API_KEY=`, `LLM_BASE_URL=https://api.deepseek.com`, `LLM_MODEL=deepseek-flash`), `config/model.yaml` with the three defaults. Run `uv sync`.
- [ ] **Step 2: Capture fixtures** (needs `SEC_USER_AGENT` exported):

```bash
F=tests/fixtures/crwv-2026q2; mkdir -p $F; A=https://www.sec.gov/Archives/edgar/data/1769628
curl -sA "$SEC_USER_AGENT" https://data.sec.gov/submissions/CIK0001769628.json -o $F/submissions.json
curl -sA "$SEC_USER_AGENT" https://data.sec.gov/api/xbrl/companyfacts/CIK0001769628.json -o $F/companyfacts.json
curl -sA "$SEC_USER_AGENT" $A/000176962826000366/crwv-20260630_htm.xml -o $F/instance.xml
curl -sA "$SEC_USER_AGENT" $A/000176962826000357/crwv-20260807.htm -o $F/8k-20260807.htm
curl -sA "$SEC_USER_AGENT" $A/000176962826000432/crwv-20260917.htm -o $F/8k-20260917.htm
curl -sA "$SEC_USER_AGENT" https://www.sec.gov/files/company_tickers.json | python3 -c "import json,sys;d=json.load(sys.stdin);json.dump({k:v for k,v in d.items() if v['ticker'] in ('CRWV','ORCL','CHTR','BRK-B')},open('$F/company_tickers.json','w'))"
```

Expected: six non-empty files; `instance.xml` ≈ 1.7 MB.

- [ ] **Step 3: Write failing tests** in `tests/test_config.py`:

```python
def test_user_agent_requires_email(monkeypatch):
    monkeypatch.setenv("SEC_USER_AGENT", "no email here")
    with pytest.raises(ConfigError, match="SEC_USER_AGENT"):
        Settings.from_env().user_agent()

def test_defaults(monkeypatch):
    for k in ["LLM_BASE_URL", "LLM_MODEL", "CREDITDASH_HOME", "LLM_MAX_INPUT_TOKENS"]:
        monkeypatch.delenv(k, raising=False)
    s = Settings.from_env()
    assert (s.llm_base_url, s.llm_model, s.llm_max_input_tokens) == ("https://api.deepseek.com", "deepseek-flash", 100000)
    assert s.home == Path(".creditdash")

def test_model_settings_file():
    assert load_model_settings() == ModelSettings(stress_haircut=0.30, horizon_years=10, ipf_max_rounds=50)
```

- [ ] **Step 4:** Run `uv run pytest tests/test_config.py -v` → FAIL (import error).
- [ ] **Step 5:** Implement `config.py` and `errors.py` per Interfaces.
- [ ] **Step 6:** Run `uv run pytest tests/test_config.py -v` → PASS.
- [ ] **Step 7: Commit** `git add -A && git commit -m "chore: scaffold creditdash with config, errors and CRWV fixtures"`

---

### Task 2: Shared models

**Files:** Create `creditdash/models.py`, `tests/test_models.py`

**Interfaces — Produces** (pydantic v2 `BaseModel`s; money in $M; dates as `datetime.date`):
- `Filing(form: str, accession: str, filed: date, period_end: date | None, primary_doc: str, items: list[str] = [])`
- `Company(ticker: str, cik: int, name: str, fiscal_year_end: str)` — `fiscal_year_end` is SEC `"MMDD"`, e.g. `"1231"`
- `Resolved(company: Company, periodic: Filing, debt_8ks: list[Filing])`
- `Fact(value: float | None, tag: str | None = None, accession: str | None = None, period: str | None = None, note: str = "")`
- `MaturityBucket(label: str, fiscal_year: int | None, months: int, principal: float, tag: str | None)` — `fiscal_year=None` and `months=0` for "Thereafter"
- `Quarter(label: str, start: date, end: date, revenue: float | None, operating_income: float | None, da: float | None, interest_expense: float | None, interest_paid: float | None, cfo: float | None, capex: float | None)` with computed property `ebitda = operating_income + da` (None if either is None)
- `Financials(period_end: date, fiscal_year_end: str, total_debt: Fact, cash: Fact, op_lease: Fact, fin_lease: Fact, rpo: Fact, shares_outstanding: Fact, maturities: list[MaturityBucket], quarters: list[Quarter], ttm: Quarter)`
- Enums (`StrEnum`): `Kind` {term_loan, delayed_draw_term_loan, revolver, senior_notes, convertible_notes, vendor_financing, finance_lease, other}; `SeniorityClass` {asset_level_secured, parent_secured, senior_unsecured, convertible, other} — **declaration order is seniority order**; `RateType` {fixed, floating, unknown}; `Amortization` {bullet, amortizing, unknown}; `ReconStatus` {pass, warn, fail, skipped}
- `Instrument` — fields exactly as spec §3.4 table; plus `verified: bool = True` (set by reconcile; serialized normally so the page can show it)
- `Extraction(period_end: date, unit_note: str, instruments: list[Instrument])`; classmethod `llm_schema() -> dict` = `model_json_schema()` with `verified` deleted from the Instrument definition's `properties` and `required`
- `SENIORITY_LABELS: dict[str, str]` = `{asset_level_secured: "Asset-level secured", parent_secured: "Parent secured", senior_unsecured: "Senior unsecured", convertible: "Convertible", other: "Other", "equity": "Equity"}`
- `Reconciliation(status: ReconStatus, reported_total: float | None, extracted_total: float | None, gap: float | None, gap_pct: float | None, scale_applied: float = 1.0, unverified: list[str] = [], maturity_diff: list[dict] = [], fit_error: float | None = None, notes: list[str] = [])`
- `ConsensusYear(fiscal_year: int, revenue: float | None, ebitda: float | None, capex: float | None, cfo: float | None)`
- `Market(sofr_pct: Fact, price: Fact, market_cap: Fact, consensus: list[ConsensusYear] = [], ratings: dict[str, str] = {}, bond_yields: dict[str, float] = {}, ebitda_label: str = "EBITDA (operating income + D&A)")`
- Model output rows: `Period(label: str, fiscal_year: int, months: int, start: date, end: date)`; `ServiceRow(instrument: str, seniority_class: SeniorityClass, period: str, principal: float, interest: float)`; `ScenarioRow(scenario: str, period: str, ebitda: float)`; `CoverageRow(scenario: str, period: str, dscr: float | None)`; `RaiseRow(period: str, capex: float, principal: float, cfo: float, to_raise: float)`; `StackClass(seniority_class: SeniorityClass | Literal["equity"], label: str, amount: float, share: float, attachment: float)`; `WaterfallRow(period: str, scenario: str, seniority_class: SeniorityClass, due: float, paid: float, shortfall: float)`
- `ModelOutput(periods: list[Period], debt_service: list[ServiceRow], scenarios: list[ScenarioRow], coverage: list[CoverageRow], raise_rows: list[RaiseRow], stack: list[StackClass], waterfall: list[WaterfallRow], excluded: list[str], fit_error: float | None)`
- `Dashboard(company: Company, filing: Filing, built_at: datetime, financials: Financials, extraction: Extraction | None, extraction_skipped: str | None, instruments: list[Instrument], reconciliation: Reconciliation, market: Market, model: ModelOutput, settings: dict)` — `instruments` is the reconciled list (incl. Unallocated)

- [ ] **Step 1: Failing tests:** `test_seniority_order` (`list(SeniorityClass)[0] == SeniorityClass.asset_level_secured` and `[-1] == SeniorityClass.other`); `test_quarter_ebitda` (`Quarter(..., operating_income=-49, da=1393, ...).ebitda == 1344`; None when `da=None`); `test_llm_schema_has_no_verified` (`"verified" not in json.dumps(Extraction.llm_schema())`); `test_dashboard_roundtrip` (build a minimal `Dashboard`, `Dashboard.model_validate_json(d.model_dump_json()) == d`).
- [ ] **Step 2:** `uv run pytest tests/test_models.py -v` → FAIL.
- [ ] **Step 3:** Implement `models.py`.
- [ ] **Step 4:** Re-run → PASS.
- [ ] **Step 5: Commit** `feat: shared pydantic models`

---

### Task 3: EDGAR client and test transport

**Files:** Create `creditdash/edgar.py`, `tests/conftest.py`, `tests/test_edgar.py`

**Interfaces:**
- Consumes: `Settings`, `SecError`, `ConfigError`.
- Produces: `class Edgar(settings: Settings, transport: httpx.BaseTransport | None = None)` with `json(url: str, cache: Path | None, max_age_s: float | None) -> dict` and `text(url: str, cache: Path | None, max_age_s: float | None) -> str`. `cache=None` → never cached; `max_age_s=None` → cache forever; `0` → always refetch. Attribute `requests: int` (network requests made).
- Produces URL helpers: `TICKERS_URL`, `submissions_url(cik: int)`, `companyfacts_url(cik: int)`, `archive_url(cik: int, accession: str, doc: str)` (accession dashes removed in the path), `instance_doc(primary_doc: str) -> str` (`"crwv-20260630.htm"` → `"crwv-20260630_htm.xml"`).
- Produces (conftest): fixture `sec_transport` → `httpx.MockTransport` serving the six fixture files by URL (unknown URL → 404) and exposing `.calls: list[str]`; fixture `settings(tmp_path, monkeypatch)` → `Settings` with `home=tmp_path/".creditdash"`, `out=tmp_path/"out"`, UA `"Test test@example.com"`.

- [ ] **Step 1: Failing tests:** `test_sends_user_agent` (MockTransport asserts header `User-Agent == "Test test@example.com"`); `test_retries_429_then_ok` (first 429, then 200 → returns JSON, `requests == 2`; patch `time.sleep`); `test_403_names_user_agent` (`SecError` message contains `"SEC_USER_AGENT"`); `test_cache_hit_makes_no_request` (second `json()` with same cache path and `max_age_s=None` → `requests == 1`); `test_max_age_zero_refetches` (`requests == 2`); `test_instance_doc` (`instance_doc("crwv-20260630.htm") == "crwv-20260630_htm.xml"`); `test_archive_url` (`archive_url(1769628, "0001769628-26-000366", "x.htm") == "https://www.sec.gov/Archives/edgar/data/1769628/000176962826000366/x.htm"`).
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement. Throttle with a module lock and `1 / sec_max_rps` minimum spacing; up to 4 attempts on 429/503 with backoff 1, 2, 4 s; cache writes are atomic (write temp, rename).
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat: EDGAR client with throttle, backoff and disk cache`

---

### Task 4: resolve

**Files:** Create `creditdash/resolve.py`, `tests/test_resolve.py`

**Interfaces:**
- Consumes: `Edgar`, `Company`, `Filing`, `Resolved`, `UnknownTicker`, `UnsupportedFiler`.
- Produces: `resolve(ticker: str, edgar: Edgar) -> Resolved`. Ticker normalized: strip, upper, `.` → `-`. Tickers file cached at `home/cache/company_tickers.json` with `max_age_s=7*86400`; submissions cached at `home/cache/<cik>/submissions.json` with `max_age_s=0`. Periodic = newest of `10-Q`/`10-K` in `filings.recent` (amendments `/A` ignored). If the newest of {10-Q, 10-K, 20-F, 40-F} is 20-F or 40-F → `UnsupportedFiler(form)`. Debt 8-Ks = `8-K` with `filed > periodic.period_end` and any of items `1.01`, `2.03`, oldest first.

- [ ] **Step 1: Failing tests** (real `Edgar` + `sec_transport`):

```python
def test_resolves_crwv(edgar):
    r = resolve(" crwv ", edgar)
    assert (r.company.cik, r.company.fiscal_year_end) == (1769628, "1231")
    assert (r.periodic.form, r.periodic.accession, r.periodic.period_end) == ("10-Q", "0001769628-26-000366", date(2026, 6, 30))
    assert [f.accession for f in r.debt_8ks] == ["0001769628-26-000357", "0001769628-26-000432"]

def test_unknown_ticker(edgar):
    with pytest.raises(UnknownTicker): resolve("ZZZZZ", edgar)

def test_dot_ticker_maps_to_dash(edgar_with_brk):  # submissions stub for BRK-B's CIK
    assert resolve("brk.b", edgar_with_brk).company.ticker == "BRK-B"

def test_foreign_filer(edgar_20f):  # synthetic submissions whose newest periodic form is 20-F
    with pytest.raises(UnsupportedFiler, match="20-F"): resolve("CRWV", edgar_20f)
```

Test-local fixtures in `tests/test_resolve.py`: `edgar_with_brk` wraps `sec_transport` and also serves a minimal submissions JSON (one 10-K) for BRK-B's CIK from `company_tickers.json`; `edgar_20f` serves a copy of the CRWV submissions with the newest `10-Q` row's form changed to `20-F`.

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat: resolve ticker to latest periodic filing and debt 8-Ks`

---

### Task 5: facts

**Files:** Create `creditdash/facts.py`, `tests/test_facts.py`

**Interfaces:**
- Consumes: `Financials`, `Fact`, `MaturityBucket`, `Quarter`.
- Produces: `build_financials(companyfacts: dict, instance_xml: str, period_end: date, fiscal_year_end: str, accession: str) -> Financials`; `bucket_label(fiscal_year: int, months: int | None, calendar: bool) -> str`.

Rules (spec §3.3): instant facts are taken at `end == period_end`; tag fallbacks as in the spec table; values converted to $M. **Shares outstanding come from the instance** (`dei:EntityCommonStockSharesOutstanding`, summed over all contexts on the cover page) because companyfacts' `dei` lacks it for CRWV.

Maturity buckets: fiscal year `FY` = the fiscal year containing `period_end` (named by the calendar year it ends in). `months` remaining in FY after `period_end`. If `...RemainderOfFiscalYear` exists at `period_end`: bucket (FY, months), then `InNextTwelveMonths`→FY+1, `InYearTwo`→FY+2, `InYearThree`→FY+3, `InYearFour`→FY+4 (12 months each). Else: `InNextTwelveMonths`→FY+1 … `InYearFive`→FY+5. Thereafter = `AfterYearFive` at `period_end`, else total − Σ dated buckets.

`bucket_label`: calendar FY → full year `"2027"`; remainder 3/6/9 months → `"Q4 26"`, `"2H26"`, `"Q2–Q4 26"`. Non-calendar → `"FY28"`; remainder → `"FY27 Q4"`, `"FY27 2H"`, `"FY27 Q2–Q4"`. `None` → `"Thereafter"`.

Quarters algorithm (last 8 fiscal quarters ending at `period_end`, fewer when the data starts later):
```
for each tag: collect duration facts, dedupe by (start, end)
q(end) = 3-month fact ending at `end` (80–100 days) if present
       else YTD(end) − YTD(previous quarter end), both starting at the same FY start
ttm = sum of the last 4 quarters per field (None if any is None); label "TTM"
```

- [ ] **Step 1: Failing tests** (CRWV fixture):

```python
def test_crwv_balances(fin):
    assert fin.total_debt.value == 35551.0 and fin.total_debt.tag == "DebtInstrumentCarryingAmount"
    assert (fin.cash.value, fin.op_lease.value, fin.rpo.value) == (6904.0, 16319.0, 103700.0)
    assert fin.shares_outstanding.value == 551_536_602

def test_crwv_maturities_shift(fin):
    assert [(b.label, round(b.principal)) for b in fin.maturities] == [
        ("2H26", 4413), ("2027", 6184), ("2028", 4416), ("2029", 2421), ("2030", 3221), ("Thereafter", 14896)]

def test_crwv_q2_quarter(fin):
    q = fin.quarters[-1]
    assert (q.label, q.revenue, q.operating_income, q.da, q.ebitda, q.interest_expense, q.cfo) == \
           ("Q2 26", 2575.0, -49.0, 1393.0, 1344.0, 558.0, 679.0)
    assert round(fin.quarters[-3].revenue, 3) == 1571.904   # Q4 25 = FY − 9M

def test_bucket_labels():
    assert bucket_label(2026, 9, True) == "Q2–Q4 26" and bucket_label(2027, 9, False) == "FY27 Q2–Q4"
    assert bucket_label(2028, 12, False) == "FY28" and bucket_label(None, None, True) == "Thereafter"

def test_non_calendar_fiscal_year():   # Review Focus 2: SYNTH_0531 is a small companyfacts dict built in the test
    # (FYE "0531"; Q1 FY27 = 2026-06-01..2026-08-31 duration facts; maturity tags at 2026-08-31 incl. RemainderOfFiscalYear)
    fin = build_financials(SYNTH_0531, "<xbrl/>", date(2026, 8, 31), "0531", "x")
    assert [b.label for b in fin.maturities][:2] == ["FY27 Q2–Q4", "FY28"]
    assert fin.quarters[-1].label == "Q1 FY27"
```

(Quarter labels: calendar `"Q2 26"`; non-calendar `"Q1 FY27"`.)

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat: XBRL facts, maturity buckets and quarterly series`

---

### Task 6: Debt footnote and 8-K text

**Files:** Create `creditdash/footnote.py`, `tests/test_footnote.py`

**Interfaces:**
- Produces: `debt_note_from_instance(instance_xml: str) -> str | None` (first present of `us-gaap:LongTermDebtTextBlock`, `DebtDisclosureTextBlock`, `DebtTextBlock`; the block's escaped HTML is unescaped, then `html_to_text`); `debt_note_from_html(filing_html: str) -> str | None` (first heading-like element — `<b>`, `<strong>`, `<h1-6>`, or a `<p>`/`<div>` whose text is ≤ 80 chars — matching `^(note\s*)?\d*\.?\s*(debt|borrowings|long-term debt|notes payable)\b` case-insensitive, up to the next element matching `^(note\s*)?\d+\.\s+\w`); `html_to_text(html: str) -> str` (tables → pipe rows `| a | b |`, empty cells dropped, `$` cells merged into the next cell, other blocks → lines, whitespace collapsed); `eight_k_items(html: str, items: tuple[str, ...] = ("1.01", "2.03")) -> str` (text from each `Item 1.01`/`Item 2.03` heading to the next `Item \d\.\d\d` heading, joined).

- [ ] **Step 1: Failing tests:**

```python
def test_crwv_note_from_instance(instance_xml):
    t = debt_note_from_instance(instance_xml)
    assert 20_000 < len(t) < 40_000 and "DDTL 4.0 Facility" in t
    assert any(l.startswith("|") and "DDTL 1.0 Facility" in l and "1,300" in l for l in t.splitlines())

def test_no_text_block():
    assert debt_note_from_instance("<xbrl></xbrl>") is None

def test_heading_fallback_borrowings():   # Review Focus 4
    html = "<p><b>Note 8. Leases</b></p><p>x</p><p><b>Note 9. Borrowings and Credit Facilities</b></p><p>Term loan due 2029</p><p><b>Note 10. Equity</b></p>"
    assert debt_note_from_html(html).splitlines() == ["Note 9. Borrowings and Credit Facilities", "Term loan due 2029"]

def test_eight_k_items(eightk_0807):
    t = eight_k_items(eightk_0807)
    assert t.startswith("Item 1.01") and "Item 2.03" in t and "Item 7.01" not in t
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement with BeautifulSoup (`lxml`). **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat: locate debt footnote and debt 8-K items as text`

---

### Task 7: LLM client (DeepSeek)

**Files:** Create `creditdash/llm.py`, `tests/test_llm.py`

**Interfaces:**
- Consumes: `Settings`, `LlmError`.
- Produces: `ask(prompt: str, system: str, schema: type[BaseModel], *, settings: Settings, transport: httpx.BaseTransport | None = None, use_cache: bool = True) -> tuple[BaseModel, dict]`. `info` keys: `ts, model, tokens_in, tokens_out, cost_usd, latency_ms, finish_reason, cached`. `PRICES = {"deepseek-flash": (0.006, 0.3, 1.2), "deepseek-v4-pro": (0.044, 1.32, 3.96)}` ($/M: cache-hit input, cache-miss input, output).

Follow research-relay's `llm.py` shape: request body `{"model", "max_tokens": 16000, "response_format": {"type": "json_object"}, "messages": [system, user]}` where system = `system + "\n\nReply with only a JSON object matching this JSON schema:\n" + json.dumps(schema_json)`; `schema_json` is `schema.llm_schema()` when defined, else `schema.model_json_schema()`. Cache file `home/cache/llm/<sha256 of sorted request JSON>.json`; log line appended to `home/llm_log.jsonl`. Raises `LlmError` when `LLM_API_KEY` is unset, when `len(system + prompt) / 4 > llm_max_input_tokens` (no request sent), or after the second failed attempt (429/5xx/transport error/`ValidationError`).

- [ ] **Step 1: Failing tests** (`MockTransport`, a 1-field `Labels` schema):
  - `test_request_body`: captured body has `response_format == {"type": "json_object"}`, system message contains `"Reply with only a JSON object"`, `max_tokens == 16000`, header `Authorization == "Bearer k"`.
  - `test_retry_invalid_then_ok`: first reply content `"{}"` (fails validation), second valid → returns object, 2 calls, `time.sleep` patched.
  - `test_two_failures_raise`: two 500s → `LlmError`.
  - `test_cache_hit`: same call twice → 1 network call, second `info["cached"] is True`, `info["cost_usd"] == 0`.
  - `test_oversize_refused`: prompt of 400_004 chars with `llm_max_input_tokens=100_000` → `LlmError` matching `"LLM_MAX_INPUT_TOKENS"`, 0 calls.
  - `test_missing_key`: `llm_api_key=None` → `LlmError` matching `"LLM_API_KEY"`.
  - `test_cost`: usage `{prompt_cache_miss_tokens: 1_000_000, completion_tokens: 1_000_000}` on `deepseek-flash` → `cost_usd == 1.5`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat: DeepSeek client with JSON mode, retry, cache and cost log`

---

### Task 8: extract

**Files:** Create `creditdash/extract.py`, `creditdash/prompts/extract_v1.md`, `tests/test_extract.py`

**Interfaces:**
- Consumes: `footnote.*`, `llm.ask`, `Resolved`, `Extraction`, `LlmError`.
- Produces: `PROMPT_VERSION = "extract_v1"`; `build_inputs(resolved: Resolved, instance_xml: str, filing_html: Callable[[], str], eight_ks: dict[str, str]) -> tuple[str | None, dict[str, str]]` returning (user prompt or None, `sources` map accession → source text used for quote checks); `extract(resolved, instance_xml, filing_html, eight_ks, *, ask: Callable = llm.ask, settings: Settings) -> tuple[Extraction | None, str | None, dict[str, str]]` → (extraction, skipped_reason, sources). `filing_html` is only called when the instance has no text block. `eight_ks` maps 8-K accession → raw HTML.

User prompt layout: `## Periodic filing <form> <accession>, period end <date>` + footnote text, then one `## 8-K <accession> filed <date>` section per debt 8-K with `eight_k_items()` text. System prompt (`extract_v1.md`) must state: amounts in **$ millions**; `principal` is the balance at period end; list every instrument in the debt table including vendor financing and finance leases if the note lists them; draws/repayments after period end go in `post_period_change` on that instrument, **recorded once** even if both the footnote's subsequent events and an 8-K mention it; a new post-period instrument has `principal: 0`; ignore Item 1.01 agreements that are not debt; `quotes` are copied **verbatim** from the given text, 1–3 per instrument, ≤ 300 chars; `source` is the accession the fields came from; unknown → null, never guessed.

- [ ] **Step 1: Failing tests** (fixture resolved CRWV; fake `ask` records its args and returns a 2-instrument `Extraction`):
  - `test_prompt_contains_note_and_8ks`: user prompt contains `"DDTL 4.0 Facility"`, `"## 8-K 0001769628-26-000357"`, `"## 8-K 0001769628-26-000432"`; `sources` keys == those two accessions + `"0001769628-26-000366"`.
  - `test_system_prompt_rules`: system text contains `"$ millions"`, `"recorded once"`, `"verbatim"`.
  - `test_filing_html_not_loaded_when_text_block_exists`: `filing_html` callable that raises is never called.
  - `test_no_footnote_skips`: instance without text block and `filing_html` returning `"<p>nothing</p>"` → `(None, "debt footnote not found", …)`, `ask` not called.
  - `test_llm_failure_skips`: `ask` raises `LlmError("boom")` → reason `"LLM failed: boom"`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat: extract debt instruments via LLM with fallbacks`

---

### Task 9: reconcile

**Files:** Create `creditdash/reconcile.py`, `tests/test_reconcile.py`

**Interfaces:**
- Consumes: `Extraction`, `Financials`, `Instrument`, `Reconciliation`, `ReconStatus`.
- Produces: `normalize(text: str) -> str` (NFKC; curly quotes → straight; en/em dashes → `-`; nbsp → space; whitespace runs → one space); `reconcile(extraction: Extraction | None, financials: Financials, sources: dict[str, str]) -> tuple[Reconciliation, list[Instrument]]`.

Rules: quote check per instrument against `sources[instrument.source]` (missing source text → unverified); unverified instruments keep `verified=False` and are named in `unverified`. Scale check before the total check: if extracted ÷ reported is within 10% of 1000 or of 0.001, multiply all money fields by the inverse, set `scale_applied`, add note `"rescaled extraction by <x>"`. Total check uses Σ `principal` (period end, not pro forma) vs `financials.total_debt`. Gap > 0 → append `Instrument(name="Unallocated", kind=other, seniority_class=other, principal=gap, rate_type=unknown, amortization=unknown, source="reconcile", quotes=[])`; gap < 0 → note `"extracted exceeds reported by <x>"`, no row. `extraction is None` → status `skipped`, one Unallocated row equal to the reported total. Maturity diff: per `financials.maturities` bucket, Σ principal of bullets maturing in that bucket vs reported (informational).

- [ ] **Step 1: Failing tests** (reported total 1000; helper `inst(name, principal, quote, source="A")`, `sources={"A": "Term Loan due 2029 … $400 million …"}`):
  - `test_normalize`: `normalize("“DDTL 4.0” — due\n 2032") == '"DDTL 4.0" - due 2032'`.
  - `test_pass_within_1pct`: Σ 995 → `pass`, Unallocated principal 5.
  - `test_warn_and_fail`: Σ 970 → `warn`; Σ 920 → `fail`.
  - `test_unverified_quote`: a quote not in the source → name in `unverified`, instrument `verified is False`, still counted.
  - `test_scale_thousands`: instruments summing 1_000_000 (thousands) → `scale_applied == 0.001`, status `pass`, note mentions rescale (Review Focus 3).
  - `test_extracted_exceeds`: Σ 1003 → `pass`, no Unallocated row, note present.
  - `test_skipped`: `reconcile(None, fin, {})` → status `skipped`, instruments == one Unallocated of 1000.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat: reconcile extraction against reported debt`

---

### Task 10: market

**Files:** Create `creditdash/market.py`, `tests/test_market.py`, `overrides/README.md`

**Interfaces:**
- Consumes: `Market`, `Fact`, `ConsensusYear`.
- Produces: `SOFR_URL = "https://markets.newyorkfed.org/api/rates/secured/sofr/last/1.json"`; `price_url(ticker) -> str` (`https://query1.finance.yahoo.com/v8/finance/chart/<T>?range=5d&interval=1d`, sent with a browser `User-Agent`); `load_overrides(ticker: str, overrides_dir: Path) -> dict`; `fetch_market(ticker: str, shares: Fact, overrides_dir: Path, transport: httpx.BaseTransport | None = None) -> Market`.

Overrides YAML keys (all optional; documented in `overrides/README.md`): `price: float`, `consensus: {<fiscal_year>: {revenue, ebitda, capex, cfo}}` ($M), `ratings: {<instrument name>: str}`, `bond_yields: {<instrument name>: pct}`, `ebitda_label: str`. Override price → Yahoo not requested. Market cap = price × shares ÷ 1e6. Any failed source → `Fact(value=None, note="<source>: <error>")`.

- [ ] **Step 1: Failing tests:** `test_sofr` (NY Fed fixture body with `percentRate: 3.87` → `sofr_pct.value == 3.87`, period `"2026-10-19"`); `test_market_cap` (Yahoo body `regularMarketPrice: 62.355`, shares 551_536_602 → `round(market_cap.value, 1) == 34391.1`); `test_override_price_skips_yahoo` (overrides `price: 60`, transport fails on any Yahoo URL); `test_yahoo_down` (500 → `price.value is None`, note contains `"yahoo"`, SOFR still set); `test_overrides_consensus` (YAML `consensus: {2027: {ebitda: 16635}}` → `ConsensusYear(fiscal_year=2027, ebitda=16635, …)`).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat: SOFR, price and overrides`

---

### Task 11: model

**Files:** Create `creditdash/model.py`, `tests/test_model.py`

**Interfaces:**
- Consumes: `Instrument`, `Financials`, `Market`, `ModelSettings`, output row models.
- Produces: `build_periods(financials: Financials, last_maturity: date, horizon_years: int) -> list[Period]` (reported buckets' dated periods, then one per fiscal year through `min(last_maturity FY, FY + horizon_years)`; the reported "Thereafter" amount is spread over these later years by the instruments maturing in them); `run_model(instruments: list[Instrument], financials: Financials, market: Market, settings: ModelSettings) -> ModelOutput`.

Conventions (decided here, spec §3.7):
- Balance = `principal + (post_period_change or 0)`. Instruments with balance ≤ 0, or with null `principal` and null `post_period_change`, or with null `maturity` → excluded (names in `excluded`), not in schedules; still shown in the stack and the table.
- Monthly simulation from the month after `period_end`. Interest each month = beginning-of-month balance × annual rate ÷ 12. Bullet repays at the end of its maturity month. Amortizing/unknown-amortization: a period's principal is paid evenly over that period's months up to maturity.
- Rate: fixed → `fixed_rate_pct`; floating → `market.sofr_pct.value + spread_pct`; else `effective_rate_pct`; else 0 and name added to `excluded` as `"<name> (no rate)"` (principal still scheduled).
- Fitting uses period-end `principal` only: start amortizing instruments straight-line over their remaining months; then repeat ≤ `ipf_max_rounds`: (a) per reported bucket, scale amortizing amounts so bullets + amortizing = reported (residual floored at 0); "Thereafter" is fitted as one aggregate over all later periods; (b) per instrument, rescale so its scheduled total = its period-end principal. `fit_error` = max |scheduled − reported| over buckets. The `post_period_change` part is then scheduled on top, unfitted: straight-line for amortizing, at maturity for bullets.
- Stack classes with amount 0 are omitted; labels from `SENIORITY_LABELS`.
- Scenarios (annual EBITDA, prorated by `months / 12` per period): `"consensus"` (only when `market.consensus` set; flat after its last year), `"ttm"` (= `financials.ttm.ebitda`), `"run-rate"` (= last quarter × 4), `"stress"` (= consensus if present else ttm, × (1 − `stress_haircut`) from FY+1; first period = base).
- DSCR = scenario EBITDA ÷ (Σ interest + Σ principal); None when denominator is 0.
- Cash to raise per period = max(0, capex + principal − cfo); capex/cfo from consensus when present, else TTM, prorated.
- Stack: classes in `SeniorityClass` order with amount = Σ balance (excluded-by-maturity instruments included), then `"equity"` = market cap when known. `share = amount ÷ total`; `attachment` = Σ share of everything junior.
- Waterfall per period and scenario: EBITDA pays each class's (interest + principal) in seniority order; `paid = min(due, remaining)`, `shortfall = due − paid`.

- [ ] **Step 1: Failing tests** (toy: period_end 2026-06-30, calendar FY; reported buckets 2H26 100, 2027 400, 2028 200, Thereafter 300; SOFR 4.0; A bullet 300 fixed 5% maturity 2027-06 senior_unsecured; B amortizing 400 floating SOFR+2 maturity 2028-12 asset_level_secured; C bullet 300 fixed 9% maturity 2031-03 convertible; market cap 1000):

```python
def test_principal_schedule(out):
    p = principal_by(out)   # {(instrument, period): principal}
    assert p[("A", "2027")] == approx(300)
    assert [p[("B", k)] for k in ("2H26", "2027", "2028")] == approx([100, 100, 200], abs=0.01)
    assert p[("C", "2031")] == approx(300) and out.fit_error < 0.01

def test_interest(out):
    i = interest_by(out)
    assert i[("A", "2H26")] == approx(7.5) and i[("A", "2027")] == approx(7.5)
    assert i[("B", "2027")] == approx(15.25, abs=0.01)   # 6% on Jan–Dec beginning balances 300 → 208.33

def test_stack_attachments(out):
    assert [(s.label, round(s.attachment, 2)) for s in out.stack] == [
        ("Asset-level secured", 0.80), ("Senior unsecured", 0.65), ("Convertible", 0.50), ("Equity", 0.0)]

def test_null_maturity_excluded():   # Review Focus 1
    out = run_model(TOY + [inst("Revolver", 50, maturity=None)], FIN, MKT, SETTINGS)
    assert "Revolver" in out.excluded and not any(r.instrument == "Revolver" for r in out.debt_service)

def test_dscr_and_raise(out):
    c = {(r.scenario, r.period): r.dscr for r in out.coverage}
    due = sum(r.interest + r.principal for r in out.debt_service if r.period == "2027")
    assert c[("ttm", "2027")] == approx(FIN.ttm.ebitda / due)
    assert all(r.to_raise >= 0 for r in out.raise_rows)

def test_waterfall_shortfall():   # TTM EBITDA 150: B due 115.25 is paid, A due 307.5 is short
    out = run_model(TOY, fin_with_ttm_ebitda(150), MKT, SETTINGS)
    w = {r.seniority_class: r for r in out.waterfall if (r.period, r.scenario) == ("2027", "ttm")}
    assert w[SeniorityClass.asset_level_secured].shortfall == 0
    assert w[SeniorityClass.senior_unsecured].shortfall == approx(307.5 - (150 - 115.25), abs=0.01)
```

- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat: debt service, scenarios, coverage, stack and waterfall model`

---

### Task 12: render shell — template, KPIs, table, method

**Files:** Create `creditdash/render.py`, `creditdash/templates/dashboard.html.j2`, `creditdash/static/dashboard.css`, `creditdash/static/dashboard.js` (KPI, table and method parts), `creditdash/static/d3.v7.min.js`, `tests/test_render.py`, `tests/sample.py`

**Interfaces:**
- Consumes: `Dashboard`.
- Produces: `render(dashboard: Dashboard) -> str` (HTML); `write_outputs(dashboard: Dashboard, out_dir: Path) -> Path` writing `<out>/<TICKER>/<period_end>/index.html` and `dashboard.json`, returning the HTML path. `tests/sample.py`: `sample_dashboard() -> Dashboard` (the Task 11 toy company run through reconcile + model), reused by Tasks 13–15.
- Section ids, in order: `kpis, stack, yield, paying, wall, coverage, raise, waterfall, instruments, method`.

Template inlines `dashboard.css`, `d3.v7.min.js` (download once: `curl -s https://cdnjs.cloudflare.com/ajax/libs/d3/7.9.0/d3.min.js -o creditdash/static/d3.v7.min.js`) and `dashboard.js`, and embeds the data as `<script id="dashboard-data" type="application/json">` with `<`, `>`, `&` escaped as `<`, `>`, `&`. JS reads it with `JSON.parse(document.getElementById("dashboard-data").textContent)` and writes all data strings with `textContent` / d3 `.text()`. Look and copy (fonts, colors as CSS variables with a dark theme, headings, notes) follow `reference/crwv-artifact/index.html`. Sections 2–8 render an empty `<div class="chart">` with a heading and note in this task.

KPIs (section 1): total debt, cash, net debt, net debt ÷ TTM EBITDA, TTM EBITDA ÷ TTM interest, operating lease liabilities, backlog, weighted average rate (disclosed effective rates weighted by balance, else coupon); reconciliation badge (pass/warn/fail/skipped with gap %); "instrument detail unavailable: <reason>" banner when `extraction_skipped`. Instrument table (section 9): sortable columns name, class, borrower, security, balance, commitment, coupon, maturity, rating (overrides), verified. Method (section 10): every `Fact` with tag + accession link, reconciliation findings, `excluded`, model settings, LLM model and prompt version.

- [ ] **Step 1: Failing tests:** `test_all_sections` (all ten ids present in order); `test_embedded_json_roundtrips` (parse the script's text → `Dashboard.model_validate` equals the input); `test_offline` (no `<script src=` and no `<link href=` in output); `test_script_injection` (instrument named `"</script><img src=x onerror=alert(1)>"` → output contains no literal `</script><img`, JSON still round-trips — Review Focus 5); `test_skipped_banner` (dashboard with `extraction_skipped="--no-llm"` contains `"instrument detail unavailable: --no-llm"`); `test_write_outputs` (path ends `CRWV/2026-06-30/index.html` for a CRWV-named sample).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** `uv run python -c "from tests.sample import sample_dashboard; from creditdash.render import write_outputs; print(write_outputs(sample_dashboard(), __import__('pathlib').Path('out')))"`; open the file in the Browser pane: KPIs, table sort and method render; console has no errors.
- [ ] **Step 6: Commit** `feat: render dashboard shell with KPIs, instrument table and method`

---

### Task 13: Charts (sections 2–8)

**Files:** Modify `creditdash/static/dashboard.js`, `creditdash/static/dashboard.css`; Test `tests/test_render.py`

**Interfaces:** Consumes the embedded `Dashboard` JSON only.

Port each chart from `reference/crwv-artifact/index.html`, replacing its `dash.data(...)` reads with fields of the embedded JSON: `stack` → capital structure (stacked bar + attachment labels; "debt only" note when no market cap); `instruments` → yield bubbles (x maturity, y = `market.bond_yields[name]` else all-in coupon, size balance; legend says which); paying (balance × rate bars, highest rate first); `debt_service` + `scenarios` → payout wall (stacked by class, scenario `<select>`, uncovered part red); `coverage` → DSCR lines with 1.0x rule; `raise_rows` → cash to raise bars; `waterfall` → year `<select>` + bars per class (paid / shortfall). Every chart: `<svg width="100%">` with a `viewBox`, redraws on resize, tooltip kept inside the page, works at 400 px width.

- [ ] **Step 1: Failing test:** `test_chart_hooks` — `dashboard.js` defines `drawStack, drawYield, drawPaying, drawWall, drawCoverage, drawRaise, drawWaterfall` and the page calls each (string checks on the rendered HTML).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** Regenerate the sample (Task 12 Step 5 command); in the Browser pane check: each of sections 2–8 contains an `<svg>` with marks, scenario and year selectors change the charts, no console errors, dark mode readable, 400 px width has no horizontal scroll.
- [ ] **Step 6: Commit** `feat: d3 charts for stack, yield, wall, coverage, raise and waterfall`

---

### Task 14: pipeline and CLI

**Files:** Create `creditdash/pipeline.py`, `creditdash/cli.py`, `tests/test_pipeline.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `STAGES = ("resolve", "fetch", "facts", "extract", "reconcile", "market", "model", "render")`; `build(ticker: str, *, settings: Settings, refresh: bool = False, no_llm: bool = False, progress: Callable[[str], None] = lambda s: None, sec_transport=None, llm_transport=None, market_transport=None) -> Path`. Fetch caching: companyfacts, instance and 8-K docs under `home/cache/<cik>/<periodic accession>/` with `max_age_s=None`; `refresh=True` passes `max_age_s=0` and `use_cache=False` to `ask`. The primary 10-Q/10-K HTML is fetched only when the instance has no debt text block.
- Produces: `main(argv: list[str] | None = None) -> int` — `build <TICKER> [--refresh] [--no-llm]` (prints the output path; on `CreditdashError` prints the message to stderr and returns its `exit_code`), `serve [--port 8765]`, `list` (prints `TICKER  period  path` for each `out/*/*/index.html`).

- [ ] **Step 1: Failing tests** (fixtures; `llm_transport` returns a small valid `Extraction` and counts calls; `market_transport` serves SOFR + price):
  - `test_build_writes_outputs`: returns `out/CRWV/2026-06-30/index.html`; `dashboard.json` validates; progress saw all `STAGES` in order.
  - `test_rebuild_is_cached` (spec §1 criterion 3): second `build` → `sec_transport.calls` grew only by the submissions URL; LLM call count unchanged.
  - `test_no_llm`: `no_llm=True` → `extraction_skipped == "--no-llm"`, LLM never called, reconciliation `skipped`.
  - `test_cli_unknown_ticker_exit_2`: monkeypatch `creditdash.cli.pipeline.build` to raise `UnknownTicker("ZZZZZ")` → `main(["build", "ZZZZZ"])` returns 2 and stderr contains `ZZZZZ`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat: pipeline orchestration and CLI`

---

### Task 15: Web app

**Files:** Create `creditdash/web.py`, `creditdash/templates/index.html.j2`, `tests/test_web.py`

**Interfaces:**
- Consumes: `pipeline.build`, `Settings`.
- Produces: `create_app(settings: Settings, build_fn=pipeline.build) -> FastAPI`. Routes: `GET /` (built dashboards list + ticker form); `POST /build` form/JSON `{ticker}` → `{"job": id}`, runs `build_fn` in a background thread; a second POST for a ticker with a running job → 409; `GET /jobs/{id}` → `{"status": "running"|"done"|"error", "stage": str, "path": str|None, "error": str|None}`; `GET /d/{ticker}/{period}` → the HTML (ticker must match `^[A-Z0-9-]{1,10}$`, period `^\d{4}-\d{2}-\d{2}$`, else 404). `serve` runs uvicorn on `127.0.0.1`.

- [ ] **Step 1: Failing tests** (`TestClient`, fake `build_fn` that writes the sample and reports stages): `test_index_lists_dashboards`; `test_build_job_flow` (POST → job id → poll until `done` → `path` set); `test_duplicate_build_409` (fake blocks on an `Event`); `test_serves_dashboard`; `test_rejects_traversal` (`/d/..%2F..%2Fetc/passwd` and `/d/CRWV/../../x` → 404).
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5:** `uv run creditdash serve`, open `http://127.0.0.1:8765` in the Browser pane, build the sample through the form, confirm it opens.
- [ ] **Step 6: Commit** `feat: local web app with build jobs`

---

### Task 16: Live acceptance and CRWV golden test

**Files:** Create `tests/fixtures/crwv-2026q2/extraction.json`, `tests/test_acceptance_crwv.py`; possibly `creditdash/prompts/extract_v2.md`

**Interfaces:** Consumes `pipeline.build`, `reconcile`.

Needs `.env` with `SEC_USER_AGENT` and `LLM_API_KEY` (copy the key from research-relay's `.env` yourself).

- [ ] **Step 1:** `uv run creditdash build CRWV` → note reconciliation status, gap %, unverified list and cost in `.creditdash/llm_log.jsonl`.
- [ ] **Step 2:** Compare with spec §1 criterion 1 and `reference/crwv-artifact/data/instruments.json`: every 10-Q facility present; DDTL 5.5, revolver draws and the Sep 2026 2.875% converts appear once each as post-period changes. If the gap exceeds 1% or post-period items double-count, revise the prompt as `extract_v2.md` (bump `PROMPT_VERSION`) and rebuild with `--refresh`; at most 3 revisions, then report what still fails.
- [ ] **Step 3:** Save the accepted DeepSeek `Extraction` as `extraction.json`; write `test_acceptance_crwv.py`: offline `reconcile(Extraction.model_validate_json(...), fin, sources)` → `abs(gap_pct) <= 0.01`, `unverified == []`, names containing `"5.5"` appear once with `post_period_change > 0`.
- [ ] **Step 4:** `uv run pytest -v` → all PASS.
- [ ] **Step 5:** `uv run creditdash build ORCL` and `uv run creditdash build CHTR` → each status `pass` or `warn`; open each in the Browser pane, no console errors. Rebuild CRWV → `llm_log.jsonl` last line has `"cached": true`.
- [ ] **Step 6: Commit** `test: CRWV golden reconciliation and live acceptance`
