import json
from datetime import date

import pytest
from pytest import approx

from creditdash import interim
from creditdash.edgar import Edgar
from creditdash.errors import LlmError
from creditdash.interim import (
    Figure, InterimStatements, find_interim, ltm, statements_text, verify, extract_interim, debt_filings,
)
from creditdash.models import Quarter
from creditdash.resolve import resolve
from tests.conftest import FIXTURES, FixtureTransport

NBIS = FIXTURES.parent / "nbis"
INTERIM = "0001104659-26-094844"
EXHIBIT = "nbis-20260812xex99d2.htm"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/1513845"
NBIS_FILES = {
    "https://www.sec.gov/files/company_tickers.json":
        json.dumps({"0": {"cik_str": 1513845, "ticker": "NBIS", "title": "Nebius Group N.V."}}).encode(),
    "https://data.sec.gov/submissions/CIK0001513845.json": (NBIS / "submissions.json").read_bytes(),
    f"{ARCHIVE}/000110465926094844/index.json": (NBIS / "6k-20260812-index.json").read_bytes(),
    f"{ARCHIVE}/000110465926094844/{EXHIBIT}": (NBIS / "6k-20260812-ex99d2.htm").read_bytes(),
}


@pytest.fixture(scope="module")
def html():
    return (NBIS / "6k-20260812-ex99d2.htm").read_text()


@pytest.fixture(scope="module")
def text(html):
    return statements_text(html)


def row(text, prefix):
    return next(line for line in text.splitlines() if line.startswith("| " + prefix))


def statements(text, **overrides) -> InterimStatements:
    figures = {
        "cash": Figure(value=8042.1, row=row(text, "Cash and cash equivalents")),
        "debt_current": Figure(value=46.7, row=row(text, "Debt, current")),
        "debt_noncurrent": Figure(value=8499.0, row=row(text, "Debt, non-current")),
        "operating_lease_liabilities": Figure(value=1510.4, row=row(text, "Operating lease liabilities, non-current")),
        "revenue_ytd": Figure(value=981.3, row=row(text, "Revenues")),
        "revenue_prior_ytd": Figure(value=156.0, row=row(text, "Revenues")),
        "operating_income_ytd": Figure(value=-303.9, row=row(text, "Loss from operations")),
        "operating_income_prior_ytd": Figure(value=-231.5, row=row(text, "Loss from operations")),
        "da_ytd": Figure(value=471.7, row=row(text, "Depreciation and amortization")),
        "da_prior_ytd": Figure(value=124.3, row=row(text, "Depreciation and amortization")),
        "interest_expense_ytd": Figure(value=182.8, row=row(text, "Interest expense")),
        "interest_expense_prior_ytd": Figure(value=4.8, row=row(text, "Interest expense")),
        "cfo_ytd": Figure(value=4504.1, row=row(text, "Net cash provided by / (used in) operating activities |")),
        "cfo_prior_ytd": Figure(value=-369.1, row=row(text, "Net cash provided by / (used in) operating activities |")),
        "capex_ytd": Figure(value=8130.3, row=row(text, "Purchases of property and equipment")),
        "capex_prior_ytd": Figure(value=1054.5, row=row(text, "Purchases of property and equipment")),
    }
    return InterimStatements(period_end=date(2026, 6, 30), ytd_months=6, unit_note="$ millions",
                             **(figures | overrides))


def test_statements_text_is_the_three_statements(text):
    assert text.startswith("UNAUDITED CONDENSED CONSOLIDATED BALANCE SHEETS")
    assert "STATEMENTS OF CASH FLOWS" in text and "NOTES TO THE UNAUDITED" not in text
    assert "​" not in text and row(text, "Revenues") == "| Revenues | 105.1 | 582.3 | 156.0 | 981.3 |"


def test_verify_keeps_figures_found_in_the_text(text):
    checked, notes = verify(statements(text), text)
    assert notes == [] and checked.capex_ytd.value == 8130.3 and checked.cfo_prior_ytd.value == -369.1


def test_verify_drops_a_number_not_in_its_row(text):
    bad = statements(text, debt_current=Figure(value=99.0, row=row(text, "Debt, current")),
                     revenue_ytd=Figure(value=981.3, row="| Revenues | 981.3 |"))
    checked, notes = verify(bad, text)
    assert checked.debt_current.value is None and checked.revenue_ytd.value is None
    assert any("debt_current" in n for n in notes) and any("revenue_ytd" in n for n in notes)


def test_ltm_rolls_the_fiscal_year_forward(text):
    fy = Quarter(label="FY25", start=date(2025, 1, 1), end=date(2025, 12, 31), revenue=529.8,
                 operating_income=-611.7, da=417.9, interest_expense=61.5, interest_paid=12.2, cfo=384.8,
                 capex=4066.0)
    t = ltm(fy, statements(text), "1231")
    assert t.label == "LTM Q2 26" and (t.start, t.end) == (date(2025, 7, 1), date(2026, 6, 30))
    assert (t.revenue, t.operating_income, t.da, t.interest_expense, t.cfo, t.capex) == approx(
        (1355.1, -684.1, 765.3, 239.5, 5258.0, 11141.8))
    assert t.ebitda == approx(81.2) and t.interest_paid is None


def test_ltm_needs_both_year_to_date_columns(text):
    fy = Quarter(label="FY25", start=date(2025, 1, 1), end=date(2025, 12, 31), revenue=529.8,
                 operating_income=None, da=None, interest_expense=None, interest_paid=None, cfo=None, capex=None)
    t = ltm(fy, statements(text, revenue_prior_ytd=Figure()), "1231")
    assert t.revenue is None


def test_find_interim_scans_6ks_for_statements(settings):
    edgar = Edgar(settings, transport=FixtureTransport(NBIS_FILES))
    r = resolve("NBIS", edgar)
    source = find_interim(edgar, r.company.cik, r.sixks, settings.home / "cache")
    assert (source.filing.accession, source.document) == (INTERIM, EXHIBIT)


def test_find_interim_none(settings):
    edgar = Edgar(settings, transport=FixtureTransport({k: v for k, v in NBIS_FILES.items() if "index" not in k}))
    r = resolve("NBIS", edgar)
    assert find_interim(edgar, r.company.cik, r.sixks, settings.home / "cache") is None


def test_debt_filings_keep_only_debt_documents(settings):
    debt_html = "<p>The Company issued $1.0 billion aggregate principal amount of 1.5% Convertible Senior Notes.</p>"
    files = NBIS_FILES | {
        f"{ARCHIVE}/000110465926112824/index.json": json.dumps({"directory": {"item": [
            {"name": "tm2626792d1_6k.htm"}, {"name": "tm2626792d1_ex99-1.htm"}]}}).encode(),
        f"{ARCHIVE}/000110465926112824/tm2626792d1_6k.htm": b"<p>Form 6-K cover</p>",
        f"{ARCHIVE}/000110465926112824/tm2626792d1_ex99-1.htm": debt_html.encode(),
        f"{ARCHIVE}/000110465926105749/index.json": json.dumps({"directory": {"item": [
            {"name": "tm2624958d1_6k.htm"}]}}).encode(),
        f"{ARCHIVE}/000110465926105749/tm2624958d1_6k.htm": b"<p>Annual general meeting results</p>",
    }
    edgar = Edgar(settings, transport=FixtureTransport(files))
    r = resolve("NBIS", edgar)
    filings, texts = debt_filings(edgar, r.company.cik, r.sixks, after=date(2026, 8, 12), exclude={INTERIM},
                                  cache_root=settings.home / "cache")
    assert [f.accession for f in filings] == ["0001104659-26-112824"]
    assert "aggregate principal amount" in texts["0001104659-26-112824"]


class FakeAsk:
    def __init__(self, result=None, error=None):
        self.calls, self.result, self.error = [], result, error

    def __call__(self, prompt, system, schema, *, settings, **kw):
        self.calls.append((prompt, system, schema))
        if self.error:
            raise self.error
        return self.result, {}


def test_extract_interim_sends_the_statements(html, text, settings):
    source = interim.InterimSource(filing=None, document=EXHIBIT, html=html)
    ask = FakeAsk(result=statements(text))
    got, reason, sent = extract_interim(source, ask=ask, settings=settings)
    assert reason is None and got.cash.value == 8042.1
    assert sent.startswith(text) and "| Total convertible debt | 10,041.8 |" in sent  # statements + debt note
    prompt, system, schema = ask.calls[0]
    assert schema is InterimStatements and "Revenues" in prompt and "verbatim" in system
    assert "CONVERTIBLE DEBT" in prompt and "principal" in system


def test_extract_interim_llm_failure(html, settings):
    source = interim.InterimSource(filing=None, document=EXHIBIT, html=html)
    got, reason, _ = extract_interim(source, ask=FakeAsk(error=LlmError("boom")), settings=settings)
    assert got is None and reason == "LLM failed: boom"


def test_apply_prefers_principal_from_the_debt_note(text):
    from creditdash.models import Fact, Financials
    fy = Quarter(label="FY25", start=date(2025, 1, 1), end=date(2025, 12, 31), revenue=529.8,
                 operating_income=-611.7, da=417.9, interest_expense=61.5, interest_paid=None, cfo=384.8, capex=4066.0)
    def fin(end):
        return Financials(period_end=end, fiscal_year_end="1231", total_debt=Fact(value=4836.8), cash=Fact(value=3721.6),
                          op_lease=Fact(value=845.4), fin_lease=Fact(value=None), rpo=Fact(value=21333.0),
                          shares_outstanding=Fact(value=253e6), maturities=[], quarters=[], ttm=fy)
    note_row = "| Total convertible debt | 10,041.8 | (1,424.8) | (118.0) | 8,499.0 |"
    s = statements(text, debt_principal=Figure(value=10041.8, row=note_row))
    source = interim.InterimSource(filing=__import__("creditdash.models", fromlist=["Filing"]).Filing(
        form="6-K", accession=INTERIM, filed=date(2026, 8, 12), primary_doc=EXHIBIT), document=EXHIBIT, html="")
    out = interim.apply(fin(date(2026, 6, 30)), fin(date(2025, 12, 31)), s, source, "1231")
    assert out.total_debt.value == 10041.8 and "8,545.7" in out.total_debt.note
    out = interim.apply(fin(date(2026, 6, 30)), fin(date(2025, 12, 31)), statements(text), source, "1231")
    assert out.total_debt.value == approx(8545.7) and "carrying" in out.total_debt.note
