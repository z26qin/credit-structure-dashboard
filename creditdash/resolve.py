"""Ticker -> company, its latest 10-Q/10-K, and the debt 8-Ks filed since that period ended."""
from __future__ import annotations

from datetime import date

from .edgar import TICKERS_URL, Edgar, submissions_url
from .errors import UnknownTicker, UnsupportedFiler
from .models import Company, Filing, Resolved

PERIODIC = ("10-Q", "10-K")
FOREIGN = ("20-F", "40-F")
DEBT_ITEMS = ("1.01", "2.03")
TICKERS_MAX_AGE_S = 7 * 86400


def resolve(ticker: str, edgar: Edgar) -> Resolved:
    symbol = ticker.strip().upper().replace(".", "-")
    home = edgar.settings.home / "cache"
    tickers = edgar.json(TICKERS_URL, home / "company_tickers.json", TICKERS_MAX_AGE_S)
    row = next((v for v in tickers.values() if v["ticker"].upper() == symbol), None)
    if row is None:
        raise UnknownTicker(f"Unknown ticker {symbol}: not in the SEC's company list")
    cik = int(row["cik_str"])

    subs = edgar.json(submissions_url(cik), home / str(cik) / "submissions.json", 0)
    filings = _filings(subs["filings"]["recent"])
    newest = max((f for f in filings if f.form in PERIODIC + FOREIGN), key=lambda f: f.filed, default=None)
    if newest is None:
        raise UnsupportedFiler(f"{symbol} has no 10-Q or 10-K on EDGAR")
    if newest.form in FOREIGN:
        raise UnsupportedFiler(f"{symbol} files {newest.form}; v1 supports 10-Q/10-K filers only")

    debt_8ks = sorted((f for f in filings if f.form == "8-K" and f.filed > newest.period_end
                       and any(i in f.items for i in DEBT_ITEMS)), key=lambda f: f.filed)
    annual = None
    if newest.form == "10-Q":
        annual = max((f for f in filings if f.form == "10-K" and f.filed <= newest.filed), key=lambda f: f.filed,
                     default=None)
    company = Company(ticker=symbol, cik=cik, name=subs.get("name") or row["title"],
                      fiscal_year_end=subs.get("fiscalYearEnd") or "1231")
    return Resolved(company=company, periodic=newest, debt_8ks=debt_8ks, annual=annual)


def _filings(recent: dict) -> list[Filing]:
    out = []
    for i, form in enumerate(recent["form"]):
        report = recent["reportDate"][i]
        items = recent.get("items", [""] * len(recent["form"]))[i]
        out.append(Filing(form=form, accession=recent["accessionNumber"][i],
                          filed=date.fromisoformat(recent["filingDate"][i]),
                          period_end=date.fromisoformat(report) if report else None,
                          primary_doc=recent["primaryDocument"][i],
                          items=[x.strip() for x in items.split(",") if x.strip()]))
    return out
