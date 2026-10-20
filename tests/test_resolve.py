import json
from datetime import date

import pytest

from creditdash.edgar import Edgar
from creditdash.errors import UnknownTicker, UnsupportedFiler
from creditdash.resolve import resolve
from tests.conftest import FIXTURES, FixtureTransport

BRK_SUBMISSIONS = json.dumps({
    "cik": "0001067983", "name": "BERKSHIRE HATHAWAY INC", "fiscalYearEnd": "1231",
    "filings": {"recent": {
        "accessionNumber": ["0000950170-26-000001"], "filingDate": ["2026-02-28"], "reportDate": ["2025-12-31"],
        "form": ["10-K"], "items": [""], "primaryDocument": ["brk-20251231.htm"]}},
}).encode()


def crwv_as_40f() -> bytes:
    subs = json.loads((FIXTURES / "submissions.json").read_text())
    recent = subs["filings"]["recent"]
    recent["form"][recent["form"].index("10-Q")] = "40-F"
    return json.dumps(subs).encode()


@pytest.fixture
def edgar_with_brk(settings):
    return Edgar(settings, transport=FixtureTransport({
        "https://data.sec.gov/submissions/CIK0001067983.json": BRK_SUBMISSIONS}))


@pytest.fixture
def edgar_40f(settings):
    return Edgar(settings, transport=FixtureTransport({
        "https://data.sec.gov/submissions/CIK0001769628.json": crwv_as_40f()}))


NBIS = FIXTURES.parent / "nbis"
NBIS_TICKERS = json.dumps({"0": {"cik_str": 1513845, "ticker": "NBIS", "title": "Nebius Group N.V."}}).encode()


@pytest.fixture
def edgar_nbis(settings):
    return Edgar(settings, transport=FixtureTransport({
        "https://www.sec.gov/files/company_tickers.json": NBIS_TICKERS,
        "https://data.sec.gov/submissions/CIK0001513845.json": (NBIS / "submissions.json").read_bytes()}))


def test_resolves_crwv(edgar):
    r = resolve(" crwv ", edgar)
    assert (r.company.ticker, r.company.cik, r.company.fiscal_year_end) == ("CRWV", 1769628, "1231")
    assert (r.periodic.form, r.periodic.accession, r.periodic.period_end) == \
        ("10-Q", "0001769628-26-000366", date(2026, 6, 30))
    assert r.periodic.primary_doc == "crwv-20260630.htm"
    assert [f.accession for f in r.debt_8ks] == ["0001769628-26-000357", "0001769628-26-000432"]
    assert r.debt_8ks[0].items == ["1.01", "2.03", "7.01", "9.01"]


def test_latest_10k_alongside_a_10q(edgar):
    r = resolve("CRWV", edgar)
    assert (r.annual.form, r.annual.accession, r.annual.period_end, r.annual.primary_doc) == \
        ("10-K", "0001769628-26-000104", date(2025, 12, 31), "crwv-20251231.htm")


def test_no_annual_when_the_periodic_is_a_10k(edgar_with_brk):
    assert resolve("BRK-B", edgar_with_brk).annual is None


def test_unknown_ticker(edgar):
    with pytest.raises(UnknownTicker, match="ZZZZZ"):
        resolve("ZZZZZ", edgar)


def test_dot_ticker_maps_to_dash(edgar_with_brk):
    r = resolve("brk.b", edgar_with_brk)
    assert (r.company.ticker, r.periodic.form, r.debt_8ks) == ("BRK-B", "10-K", [])


def test_40f_filer_is_explained(edgar_40f):
    with pytest.raises(UnsupportedFiler, match=r"40-F.*20-F filers that report in US GAAP"):
        resolve("CRWV", edgar_40f)


def test_20f_filer_resolves_with_later_6ks(edgar_nbis):
    r = resolve("nbis", edgar_nbis)
    assert (r.periodic.form, r.periodic.accession, r.periodic.period_end) == \
        ("20-F", "0001104659-26-052948", date(2025, 12, 31))
    assert r.annual is None and r.debt_8ks == []
    assert all(f.form == "6-K" and f.filed > date(2025, 12, 31) for f in r.sixks)
    assert [f.filed for f in r.sixks] == sorted((f.filed for f in r.sixks), reverse=True)
    assert "0001104659-26-094844" in [f.accession for f in r.sixks]


def test_tickers_and_submissions_cached_as_specified(edgar, sec_transport, settings):
    resolve("CRWV", edgar)
    resolve("CRWV", edgar)
    assert sec_transport.calls.count("https://www.sec.gov/files/company_tickers.json") == 1
    assert sec_transport.calls.count("https://data.sec.gov/submissions/CIK0001769628.json") == 2
    assert (settings.home / "cache" / "company_tickers.json").exists()
