"""20-F filers: interim results from 6-K exhibits, which carry no XBRL.

`find_interim` scans the 6-Ks filed after the 20-F for an exhibit with financial statements; the model reads
the three statements into `InterimStatements`; `verify` keeps only figures whose row appears in the text and
contains the number; `ltm` rolls the 20-F year forward: fiscal year + year to date − prior year to date.
`debt_filings` picks later 6-Ks that announce debt, for the instrument extraction.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Callable

from pydantic import BaseModel, Field

from . import llm
from .config import Settings
from .edgar import Edgar, archive_url
from .errors import LlmError, SecError
from .facts import _month_end_shift, quarter_label
from .footnote import debt_note_from_html, html_to_text
from .models import Fact, Filing, Financials, Quarter
from .reconcile import normalize

PROMPT_VERSION = "interim_v3"
PROMPT_FILE = Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md"
MAX_SCAN = 12  # 6-Ks to open, newest first, looking for statements
MAX_DEBT_6KS = 10  # kept
MAX_SCANNED_6KS = 40  # looked at, newest first
MAX_DOC_BYTES = 5_000_000
MAX_STATEMENT_CHARS = 60_000
MAX_NOTE_CHARS = 30_000
DEBT_WINDOW = (2_000, 10_000)  # characters kept before and after the first debt mention in a 6-K document
STATEMENTS = re.compile(r"condensed consolidated balance sheets?", re.I)
NOTES = re.compile(r"^notes to (the )?(unaudited )?(interim )?(condensed )?consolidated financial statements", re.I)
DEBT_WORDS = re.compile(r"aggregate principal amount|convertible senior notes|senior (secured )?notes due|"
                        r"credit agreement|term loan|loan facility|indenture|notes offering", re.I)
NUMBER = re.compile(r"\(?-?\d[\d,]*(?:\.\d+)?\)?")


class Figure(BaseModel):
    value: float | None = None
    row: str | None = Field(default=None, description="the table row the value is read from, verbatim")


class InterimStatements(BaseModel):
    period_end: date
    ytd_months: int
    unit_note: str
    cash: Figure = Figure()
    debt_current: Figure = Figure()
    debt_noncurrent: Figure = Figure()
    operating_lease_liabilities: Figure = Figure()
    debt_principal: Figure = Figure()  # from the debt note; the balance sheet shows carrying amounts
    revenue_ytd: Figure = Figure()
    revenue_prior_ytd: Figure = Figure()
    operating_income_ytd: Figure = Figure()
    operating_income_prior_ytd: Figure = Figure()
    da_ytd: Figure = Figure()
    da_prior_ytd: Figure = Figure()
    interest_expense_ytd: Figure = Figure()
    interest_expense_prior_ytd: Figure = Figure()
    cfo_ytd: Figure = Figure()
    cfo_prior_ytd: Figure = Figure()
    capex_ytd: Figure = Figure()
    capex_prior_ytd: Figure = Figure()
    sbc_ytd: Figure = Figure()
    sbc_prior_ytd: Figure = Figure()
    revenue_qtr: Figure = Figure()  # the three months ending period_end, for the run-rate
    operating_income_qtr: Figure = Figure()
    da_qtr: Figure = Figure()
    sbc_qtr: Figure = Figure()

    def figures(self) -> dict[str, Figure]:
        return {k: v for k, v in self if isinstance(v, Figure)}


@dataclass
class InterimSource:
    filing: Filing | None
    document: str
    html: str


# --- finding the documents ----------------------------------------------------------------------

def _documents(edgar: Edgar, cik: int, filing: Filing, cache_root: Path):
    """(name, html) for each HTML document of a filing, from its index.json; unreachable ones are skipped."""
    folder = cache_root / str(cik) / filing.accession
    try:
        index = edgar.json(archive_url(cik, filing.accession, "index.json"), folder / "index.json", None)
    except SecError:
        return
    for item in index.get("directory", {}).get("item", []):
        name = item.get("name", "")
        size = str(item.get("size") or "0")
        if not name.lower().endswith((".htm", ".html")) or "index" in name.lower():
            continue
        if size.isdigit() and int(size) > MAX_DOC_BYTES:
            continue
        try:
            yield name, edgar.text(archive_url(cik, filing.accession, name), folder / name, None)
        except SecError:
            continue


def _plain(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html))


def has_statements(html: str) -> bool:
    text = _plain(html)
    return bool(STATEMENTS.search(text)) and bool(re.search(r"statements? of cash flows", text, re.I))


def find_interim(edgar: Edgar, cik: int, sixks: list[Filing], cache_root: Path) -> InterimSource | None:
    for filing in sixks[:MAX_SCAN]:
        for name, html in _documents(edgar, cik, filing, cache_root):
            if has_statements(html):
                return InterimSource(filing=filing, document=name, html=html)
    return None


def debt_filings(edgar: Edgar, cik: int, sixks: list[Filing], after: date, exclude: set[str],
                 cache_root: Path) -> tuple[list[Filing], dict[str, str]]:
    """6-Ks filed after `after` with a document announcing debt; text kept around the first debt mention."""
    filings, texts = [], {}
    for filing in [f for f in sixks if f.filed > after and f.accession not in exclude][:MAX_SCANNED_6KS]:
        if len(filings) == MAX_DEBT_6KS:
            break
        parts = []
        for _, html in _documents(edgar, cik, filing, cache_root):
            text = html_to_text(html)
            if m := DEBT_WORDS.search(text):
                before, after_chars = DEBT_WINDOW
                parts.append(text[max(0, m.start() - before): m.start() + after_chars])
        if parts:
            filings.append(filing)
            texts[filing.accession] = "\n\n".join(parts)
    return sorted(filings, key=lambda f: f.filed), texts


# --- reading the statements ----------------------------------------------------------------------

def statements_text(html: str) -> str | None:
    """The balance sheet through the statement of cash flows, as text; the notes are left out."""
    lines = html_to_text(html).splitlines()
    # the statement's own title, not a table-of-contents row ("| Unaudited ... Balance Sheets ... | F-2 |")
    start = next((i for i, line in enumerate(lines)
                  if STATEMENTS.search(line) and len(line) < 120 and not line.startswith("|")), None)
    if start is None:
        return None
    end = next((j for j in range(start + 1, len(lines)) if NOTES.match(lines[j])), len(lines))
    return "\n".join(lines[start:end])[:MAX_STATEMENT_CHARS]


def extract_interim(source: InterimSource, *, ask: Callable = llm.ask, settings: Settings,
                    use_cache: bool = True) -> tuple[InterimStatements | None, str | None, str]:
    text = statements_text(source.html)
    if not text:
        return None, "no financial statements in the 6-K exhibit", ""
    note = debt_note_from_html(source.html)
    if note:
        text = f"{text}\n\n{note[:MAX_NOTE_CHARS]}"
    f = source.filing
    head = f"## 6-K {f.accession} filed {f.filed}, exhibit {source.document}" if f else f"## {source.document}"
    try:
        stmts, _ = ask(f"{head}\n\n{text}", PROMPT_FILE.read_text(), InterimStatements, settings=settings,
                       use_cache=use_cache)
    except LlmError as e:
        return None, f"LLM failed: {e}", text
    return stmts, None, text


def _numbers(row: str) -> list[float]:
    out = []
    for token in NUMBER.findall(row):
        negative = token.startswith("(") or token.startswith("-")
        digits = token.strip("()-").replace(",", "")
        try:
            out.append(-float(digits) if negative else float(digits))
        except ValueError:
            continue
    return out


def verify(stmts: InterimStatements, text: str) -> tuple[InterimStatements, list[str]]:
    """Keep a figure only if its row is in the text and the row shows the number (sign aside)."""
    plain = normalize(text)
    checked, notes = stmts.model_copy(deep=True), []
    for name, fig in stmts.figures().items():
        if fig.value is None:
            continue
        ok = bool(fig.row) and normalize(fig.row) in plain and any(
            abs(abs(n) - abs(fig.value)) < 0.051 for n in _numbers(fig.row))
        if not ok:
            setattr(checked, name, Figure())
            notes.append(f"{name}: {fig.value:g} was not found in its row of the 6-K text, so it is left out")
    return checked, notes


def ltm(annual: Quarter, s: InterimStatements, fiscal_year_end: str) -> Quarter:
    """Last twelve months = fiscal year + year to date − the same period a year earlier."""
    def roll(field: str, magnitude: bool = False) -> float | None:
        fy, ytd, prior = getattr(annual, field), getattr(s, f"{field}_ytd").value, getattr(s, f"{field}_prior_ytd").value
        if fy is None or ytd is None or prior is None:
            return None
        return fy + abs(ytd) - abs(prior) if magnitude else fy + ytd - prior

    end = s.period_end
    start = date(end.year - 1, end.month, end.day) + timedelta(days=1) if end.month != 2 or end.day != 29 \
        else date(end.year - 1, 3, 1)
    return Quarter(label=f"LTM {quarter_label(end, fiscal_year_end)}", start=start, end=end,
                   revenue=roll("revenue"), operating_income=roll("operating_income"), da=roll("da", True),
                   interest_expense=roll("interest_expense", True), interest_paid=None, cfo=roll("cfo"),
                   capex=roll("capex", True), sbc=roll("sbc", True))


def latest_quarter(s: InterimStatements, fiscal_year_end: str) -> Quarter | None:
    """The three months ending at the period end, from the three-month columns: the run-rate's quarter."""
    oi, da = s.operating_income_qtr.value, s.da_qtr.value
    if oi is None or da is None:
        return None
    end, sbc = s.period_end, s.sbc_qtr.value
    return Quarter(label=quarter_label(end, fiscal_year_end), start=_month_end_shift(end, -3) + timedelta(days=1),
                   end=end, revenue=s.revenue_qtr.value,
                   operating_income=oi, da=abs(da), interest_expense=None, interest_paid=None, cfo=None, capex=None,
                   sbc=None if sbc is None else abs(sbc))


def apply(base: Financials, annual: Financials, s: InterimStatements, source: InterimSource,
          fiscal_year_end: str) -> Financials:
    """Interim balances and last-twelve-month figures over `base` (dated at the interim period end); what the
    6-K lacks comes from the 20-F, marked as such."""
    acc = source.filing.accession
    tag = f"6-K {acc} ({source.document}, unaudited)"

    def fact(value: float, note: str = "") -> Fact:
        return Fact(value=value, tag=tag, accession=acc, period=s.period_end.isoformat(), note=note)

    def from_20f(f: Fact) -> Fact:
        return f.model_copy(update={"note": f"from the 20-F at {annual.period_end}" + (f"; {f.note}" if f.note else "")})

    debt = [x.value for x in (s.debt_current, s.debt_noncurrent) if x.value is not None]
    carrying = sum(debt) if debt else None
    if s.debt_principal.value is not None:  # principal, as the instrument list is, so the totals compare
        total_debt = fact(s.debt_principal.value, "principal outstanding per the debt note" +
                          (f"; balance-sheet carrying amount {carrying:,.1f}" if carrying is not None else ""))
    elif carrying is not None:
        total_debt = fact(carrying, "carrying amount, current + non-current")
    else:
        total_debt = from_20f(annual.total_debt)
    ttm = ltm(annual.ttm, s, fiscal_year_end)
    if ttm.revenue is None and ttm.operating_income is None:
        ttm = annual.ttm
    return base.model_copy(update={
        "total_debt": total_debt,
        "cash": fact(s.cash.value) if s.cash.value is not None else from_20f(annual.cash),
        "op_lease": (fact(s.operating_lease_liabilities.value) if s.operating_lease_liabilities.value is not None
                     else from_20f(annual.op_lease)),
        "fin_lease": from_20f(annual.fin_lease),
        "rpo": from_20f(annual.rpo),
        "shares_outstanding": annual.shares_outstanding,
        "quarters": [q] if (q := latest_quarter(s, fiscal_year_end)) else [],
        "ttm": ttm,
    })
