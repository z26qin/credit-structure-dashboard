# SDD ledger — plan: docs/superpowers/plans/2026-10-20-credit-structure-dashboard.md
Spec: docs/superpowers/specs/2026-10-20-credit-structure-dashboard-design.md
Setup: Ruling: implement on main, no worktree — user chose "current branch" at propose_branch; fresh repo with only docs — cost if wrong: history mixes docs and code on main
Setup: Ruling: fixtures captured with placeholder SEC_USER_AGENT "creditdash-dev dev@example.com" — no .env yet; user's email not sent to SEC without asking — cost if wrong: SEC may throttle a handful of requests
Pre-flight rows:
- T2→T5 Financials/Quarter/MaturityBucket: fields match (interest_paid added in review)
- T3→T4/T14 Edgar.json/text(url, cache, max_age_s), .requests, sec_transport.calls: match
- T6→T8 footnote.* names: match
- T7→T8 ask(prompt, system, schema, *, settings, transport, use_cache): fake ask in T8 must accept settings kw — noted
- T2→T7 Extraction.llm_schema(): T7 prefers llm_schema when defined: match
- T9→T11 Unallocated row has maturity None → T11 excludes it from schedules, keeps it in stack: consistent with spec (gap visible in stack) — noted
- T10→T11 Market.sofr_pct.value: match
- T11→T12 ModelOutput fields: match
- T5→T14 build_financials(..., accession): match
- T12→T14 write_outputs(dashboard, out_dir): match
Task 1: Ruling: config.ENV_FILE + autouse conftest fixture so tests never read the developer's .env — test_defaults would break once a real .env sets CREDITDASH_HOME — cost if wrong: none
Task 1: complete (commits 3fee526..21c10bc, tests: uv run pytest tests/test_config.py -q → 4 passed in 0.01s)
Task 2: Ruling: ReconStatus member is pass_ (value "pass") — 'pass' is a Python keyword — cost if wrong: none
Task 2: complete (commits 21c10bc..1e97674, tests: uv run pytest tests/test_models.py -q → 4 passed in 0.02s)
Task 3: complete (commits 1e97674..7507803, tests: uv run pytest -q → 17 passed in 0.03s)
Task 4: complete (commits 7507803..c47e85c, tests: uv run pytest -q → 22 passed in 0.05s)
Task 5: Ruling: total-debt fallback is LongTermDebt (+ShortTermBorrowings), else LongTermDebtNoncurrent+LongTermDebtCurrent (+STB) — spec's LongTermDebt+LongTermDebtCurrent double counts (CRWV 3/31: 17312+7547=24859=LongTermDebt); test_long_term_debt_fallback_does_not_double_count_current — cost if wrong: fallback total off for filers tagging LongTermDebt as noncurrent only
Task 5: Ruling: fact matching uses ±7-day tolerance on start/end dates — 52/53-week fiscal years — cost if wrong: a stray fact could match a neighbouring period
Task 5: complete (commits c47e85c..aaee455, tests: uv run pytest -q → 29 passed in 0.06s)
Task 6: Ruling: debt_note_from_html picks the first heading match whose section is ≥200 chars, else the longest — skips table-of-contents entries; test_heading_fallback_skips_table_of_contents — cost if wrong: an MD&A 'Debt' subsection over 200 chars before the note would be chosen
Task 6: complete (commits aaee455..315d6de, tests: uv run pytest -q → 38 passed in 0.12s)
Task 7: Ruling: 4xx other than 429 is not retried (test_client_error_not_retried) — research-relay's rule; an auth error retried twice wastes time — cost if wrong: none
Task 7: complete (commits 315d6de..6653339, tests: uv run pytest -q → 47 passed in 0.16s)
Task 8: Ruling: extract() gains use_cache kwarg passed to ask — Task 14's --refresh needs to bypass the LLM cache — cost if wrong: none
Task 8: Ruling: prompt classes SPV/subsidiary borrowings secured by their own assets as asset_level_secured even when parent-guaranteed (guarantee in recourse_to_parent) — matches the CRWV artifact's CLO view — cost if wrong: guaranteed SPV loans shown more senior than an analyst might
Task 8: complete (commits 6653339..bfce385, tests: uv run pytest -q → 52 passed in 0.25s)
Task 9: Ruling: no reported total → status warn with note, no Unallocated row; instruments without quotes are unverified — spec silent — cost if wrong: badge says warn rather than skipped
Task 9: complete (commits bfce385..2e76ef3, tests: uv run pytest -q → 63 passed in 0.26s)
Task 10: Ruling: malformed overrides YAML raises ConfigError naming the file (build stops) — a typo in user-written input should be visible, not silently ignored — cost if wrong: one bad file blocks a build until fixed
Task 10: complete (commits 2e76ef3..b7c50fc, tests: uv run pytest -q → 71 passed in 0.27s)
Task 11: Ruling: toy fixtures live in tests/toy.py (Task 12's sample.py reuses them) — plan put them in sample.py later, Task 11 needed them first — cost if wrong: none
Task 11: Ruling: stress haircut applies to every period after the first (10-K case: first period FY+1 stays at base) — plan's "first period = base" — cost if wrong: one year of stress is milder for 10-K filers
Task 11: Ruling: a maturity already past at period end is treated as due in the first month; negative post_period_change is repaid in month 1 and the rest of the schedule scaled down — spec silent — cost if wrong: timing of a few payments
Task 11: Ruling: consensus for the current (partial) fiscal year = FY consensus − reported quarters in that FY (CRWV artifact's 2H26 method); applies to EBITDA, capex, CFO — cost if wrong: partial-year values off if quarters are missing
Task 11: complete (commits b7c50fc..8eaaf4a, tests: uv run pytest -q → 84 passed in 0.29s)
Task 12: Ruling: browser checks go through a local static server (.claude/launch.json "out-static") — the pane cannot open file:// paths — cost if wrong: none
Task 12: Ruling: company name, form and filing metadata are server-rendered by Jinja with autoescape; all instrument data is drawn by JS via textContent (test_company_name_escaped, test_script_injection) — cost if wrong: none
Task 12: complete (commits 8eaaf4a..fe5a7bb, tests: uv run pytest -q → 91 passed in 0.34s)
Task 13: Ruling: coverage chart caps the y-axis at 10x (higher values drawn at the cap, tooltip says off scale) — one tiny-debt year would flatten every other line — cost if wrong: none
Task 13: complete (commits fe5a7bb..5739529, tests: uv run pytest -q → 92 passed in 0.33s)
Task 14: Ruling: build() also takes overrides_dir and model_settings (defaults overrides/ and config/model.yaml) — tests must not read the repo's overrides — cost if wrong: none
Task 14: Ruling: a missing XBRL instance (404) is tolerated: instance = "" so the footnote falls back to the filing HTML and shares are unknown — older non-iXBRL filers — cost if wrong: none
Task 14: complete (commits 5739529..45adef7, tests: uv run pytest -q → 99 passed in 0.72s)
Task 15: Ruling: found during the live web check — spec §5 says the totals-only dashboard keeps the maturity wall, coverage and cash-to-raise, but the Unallocated row (no maturity) was excluded from the model so those charts were empty. Unallocated now takes the reported schedule's residual after known instruments, Thereafter spread over the 5 years after the last dated bucket, at the known instruments' average rate else TTM interest ÷ debt; test_unallocated_follows_reported_schedule + test_unallocated_takes_only_the_residual RED→GREEN, suite 109/109 — cost if wrong: gap debt's timing beyond the dated buckets is an assumption (shown in the method section)
Task 15: Ruling: POST /build takes JSON only (no form encoding) — avoids adding python-multipart; the page posts JSON — cost if wrong: curl users must send JSON
Task 15: Ruling: CLI serve default stays 8765 per spec though another local process uses it; preview used --port 8777 — cost if wrong: user passes --port
Task 15: complete (commits 45adef7..585fc42, tests: uv run pytest -q → 109 passed in 0.63s)
Task 16: .env written from ~/.podmem/.env (SEC_USER_AGENT, LLM_API_KEY) with the user's explicit OK; gitignored, chmod 600
Task 16: Ruling: root cause of the first live failure — deepseek-flash spent all 16,000 max_tokens on hidden reasoning (finish_reason=length, empty content). Now max_tokens 32,000 + reasoning_effort "low" (LLM_REASONING_EFFORT), and a cut-off reply is retried once with thinking disabled; test_cut_off_retries_without_thinking / test_cut_off_twice_names_the_cause RED→GREEN — cost if wrong: ~60–80 s per new filing
Task 16: Ruling: facts fallbacks found on ORCL/CHTR — total debt also from LongTermNotesAndLoans/NotesPayableCurrent/DebtCurrent pairs, D&A = Depreciation + AmortizationOfIntangibleAssets, interest from −InterestIncomeExpenseNonoperatingNet; maturity schedule from the latest 10-K when the 10-Q has none (current FY's amount placed in its remaining months) — 4 facts tests RED→GREEN — cost if wrong: 10-K schedule overstates the current year by payments already made
Task 16: Ruling: spec extension — the latest 10-K's debt note is sent alongside the 10-Q's (ORCL's 10-Q has no debt note; CHTR's and CRWV's 10-Qs lack instrument detail); each instrument once, 10-Q balances when listed. Needed for spec §1 criterion 2 — cost if wrong: ~2x input tokens (still ~$0.02–0.03 per filing)
Task 16: Ruling: Review Focus 1 changed — instruments with a balance but no maturity (category totals, undated revolvers) now share the reported schedule's residual by balance at their own rate instead of being excluded (spec §5 wants charts on totals); test_null_maturity_follows_the_reported_schedule — cost if wrong: timing of undated debt is an assumption, stated in the method section
Task 16: Ruling: prompt revisions v2 (10-K source), v3 (keep category totals), v4 (a facility's size is a commitment, not a draw) — 3 revisions, the plan's limit — cost if wrong: n/a
Task 16: Ruling: quotes verify against any filing text sent to the model, not only the instrument's `source` (balances from the 10-Q, coupon quoted from the 10-K) — test_quote_from_another_sent_filing_verifies RED→GREEN — cost if wrong: a quote from the wrong filing still counts as real text
Task 16: Ruling: CRWV golden expectation follows the filing, not the artifact — the Aug 2026 8-K announces a $2.6B DDTL 5.5 commitment and reports no draw, so commitment 2600 / post-period change 0; the artifact's "post-quarter draws" are not in SEC filings — cost if wrong: if a draw was disclosed elsewhere (e.g. the Q3 10-Q), the next filing will show it
Task 16: Ruling: fit_error is measured on the final schedule (after undated debt fills the residual) — CHTR showed a misleading $61B — cost if wrong: none
Task 16: live results (prompt extract_v4, deepseek-flash): CRWV pass 0.00% gap, 0 unverified, $0.01–0.03/call; ORCL warn −3.8% (10-K list vs lower 10-Q total), 0 unverified; CHTR pass 0.56%, 0 unverified; rebuilds served from cache (criterion 3); no console errors on any page
Task 16: complete (commits 585fc42..e7e1f50, tests: uv run pytest -q → 126 passed in 0.88s)
Final review: fresh reviewer (model fable) on 3fee526..e7e1f50 — verdict "With fixes"; 0 Critical, 7 Important, 13 Minor; all 7 Important re-graded as Important by effect (wrong or unflagged numbers)
Final: fixed matured-before-period-end instruments shown as outstanding (ORCL $3B 2.65% July 2026 note) — excluded from stack/schedule, noted in reconciliation, header banner, 10-K current-year bucket reduced — test_matured_before_period_end_is_not_scheduled + test_matured_before_period_end_noted RED→GREEN, suite 138/138
Final: fixed missing capex treated as zero in cash-to-raise — capex fallback tags, no rows without capex — test_raise_needs_capex + test_capex_fallback_tag RED→GREEN, suite 138/138
Final: fixed fiscal_year_of off by one year for 52/53-week years ending in early January — test_fiscal_year_of_early_january_52_53_week RED→GREEN, suite 138/138
Final: fixed stress haircut skipping FY+1 for 10-K filers (Task 11 ruling reversed; spec §3.7 "from FY+1" binds) — test_stress_haircut_starts_the_year_after_a_10k RED→GREEN, suite 138/138
Final: fixed non-YYYY-MM maturities silently undated — Instrument.maturity normalizes YYYY-MM-DD, YYYY, "Dec 2030", "September 1, 2031" — test_maturity_normalized RED→GREEN, suite 138/138
Final: fixed floating loans at 0% when SOFR is down — overrides `sofr`, last fetched SOFR cached and reused with a note, no-rate instruments labeled "interest not modeled" with a header banner — test_sofr_cached_and_reused_when_down + test_sofr_override + test_floating_without_sofr_is_flagged_as_unmodeled_interest + test_unmodeled_interest_banner RED→GREEN, suite 138/138
Final: fixed a single-note DebtInstrumentCarryingAmount taken as total debt — under 90% of balance-sheet debt, the balance-sheet total is used with a note (spec table gap) — test_carrying_amount_for_one_note_is_not_total_debt RED→GREEN, suite 138/138
Final: minor (deferred): service rows keyed by instrument name — two same-named instruments merge (model.py _simulate)
Final: minor (deferred): KPI sublabel says "pro forma" when the difference is extraction overshoot, not post-period changes (dashboard.js drawKpis)
Final: minor (deferred): weighted average rate uses only disclosed effective rates when any exist (CHTR: 2 of 15); mix bases per instrument
Final: minor (deferred): method shows "largest bucket error" without naming the bucket or the cause (CHTR $4.5B: post-period redemptions vs older 10-K schedule)
Final: minor (deferred): llm.py retries a finish_reason=length reply with partial JSON unchanged; treat any length cut-off as _CutOff; TIMEOUT_S 240 vs spec 180 not ledgered (now noted here)
Final: minor (deferred): POST /build accepts any Content-Type, so another tab could trigger builds; require application/json; jobs dict grows unbounded
Final: minor (deferred): _shares sums every dei:EntityCommonStockSharesOutstanding context; multi-registrant filings overstate shares
Final: minor (deferred): _ttm does not check the last four quarters are contiguous
Final: minor (deferred): bad override values (consensus: {FY27: …}) raise ValueError, not ConfigError naming the file
Final: minor (deferred): prompt does not require a quote supporting a non-zero post_period_change (CHTR −$5,499 verified via a balance row)
Final: minor (deferred): spec §3.8 puts the filing link in the header; it is only in the method section
Final: minor (deferred): edgar.Edgar never closes its httpx.Client (ResourceWarning)
Final: minor (deferred): _month_list starts at period_end + 1 for non-month-end period ends, shifting the window one month
Final: Ruling: fiscal years named by the calendar year they end in (retail "fiscal 2024" convention differs) — spec §3.3 — cost if wrong: labels differ from a retailer's own naming
Final: Ruling: InNextTwelveMonths on a 10-Q without RemainderOfFiscalYear read as FY+1 — spec §3.3 — cost if wrong: one bucket shifted for rolling-12-month taggers
Final: Ruling: floating rate = SOFR + spread regardless of benchmark (EUR notes) or a different disclosed effective rate — spec §3.7 — cost if wrong: interest on EUR/odd-benchmark floaters off by the basis
Final: Ruling: total check compares principal to LongTermDebt carrying value when no DebtInstrumentCarryingAmount — spec table — cost if wrong: OID-heavy issuers show warn
Final: Ruling: parent-guaranteed SPV / opco secured debt classed asset_level_secured — Task 8 ruling, analyst judgment, recourse shown separately — cost if wrong: seniority one notch high
Final: Ruling: 10-K schedule's current-year bucket includes payments already made — method section says so; matured instruments now subtracted — cost if wrong: current-year bucket overstated by amortization since the 10-K
Final: Ruling: undated debt's Thereafter share spread over 5 years (ORCL commercial paper over a decade) — stated in the method — cost if wrong: short-term paper shown as long-dated
Final: Ruling: MAX_TOKENS 32,000 + reasoning_effort low vs spec 16,000 — Task 16 root cause; reasoning_effort may be rejected by non-DeepSeek base URLs — cost if wrong: a provider switch needs llm.py changes (already the design)
Final: Ruling: POST /build JSON-only and serve default 8765 — Task 15 rulings stand — cost if wrong: none
Final: Ruling: CRWV golden follows the 8-K (DDTL 5.5 commitment, no draw) — Task 16 ruling stands
Final: Ruling: undated/Unallocated instruments follow the reported schedule (Review Focus 1 change) — reviewer agrees — cost if wrong: timing assumption, stated
Final: Ruling: prompt v1–v3 kept for history — reviewer agrees
