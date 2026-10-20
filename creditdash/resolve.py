"""Ticker -> company, its latest 10-Q/10-K (or 20-F), and the filings since that may change its debt."""
from __future__ import annotations

from datetime import date

from .edgar import TICKERS_URL, Edgar, submissions_url
from .errors import UnknownTicker, UnsupportedFiler
from .models import Company, Filing, Resolved

PERIODIC = ("10-Q", "10-K", "20-F")
UNSUPPORTED = ("40-F",)
DEBT_ITEMS = ("1.01", "2.03")
TICKERS_MAX_AGE_S = 7 * 86400
MAX_6KS = 30


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
    name = subs.get("name") or row["title"]
    newest = max((f for f in filings if f.form in PERIODIC + UNSUPPORTED), key=lambda f: f.filed, default=None)
    if newest is None:
        raise UnsupportedFiler(f"{symbol} ({name}) has no 10-K, 10-Q or 20-F on EDGAR")
    if newest.form in UNSUPPORTED:
        raise UnsupportedFiler(f"{symbol} ({name}) files {newest.form}, the Canadian cross-border annual report "
                               f"(latest filed {newest.filed}); creditdash supports 10-K/10-Q filers and "
                               "20-F filers that report in US GAAP.")
    if newest.form == "20-F":  # a foreign private issuer: quarterly results arrive on 6-K, without XBRL
        sixks = sorted((f for f in filings if f.form == "6-K" and f.filed > newest.period_end),
                       key=lambda f: f.filed, reverse=True)[:MAX_6KS]
        company = Company(ticker=symbol, cik=cik, name=name, fiscal_year_end=subs.get("fiscalYearEnd") or "1231")
        return Resolved(company=company, periodic=newest, debt_8ks=[], sixks=sixks)

    debt_8ks = sorted((f for f in filings if f.form == "8-K" and f.filed > newest.period_end
                       and any(i in f.items for i in DEBT_ITEMS)), key=lambda f: f.filed)
    annual = None
    if newest.form == "10-Q":
        annual = max((f for f in filings if f.form == "10-K" and f.filed <= newest.filed), key=lambda f: f.filed,
                     default=None)
    company = Company(ticker=symbol, cik=cik, name=name, fiscal_year_end=subs.get("fiscalYearEnd") or "1231")
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
