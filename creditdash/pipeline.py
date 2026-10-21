"""ticker -> resolve -> fetch -> facts -> extract -> reconcile -> market -> model -> render -> index.html

The model stage also builds the top card: the coverage tests (headline.py) and the market read (creditrisk.py).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Callable

import httpx

from . import creditrisk, interim, llm
from .config import ModelSettings, Settings, load_model_settings
from .edgar import Edgar, archive_url, companyfacts_url, instance_doc
from .errors import SecError, UnsupportedFiler
from .extract import PROMPT_VERSION, extract
from .facts import build_financials, fiscal_year_of
from .interim import debt_filings, extract_interim, find_interim, verify
from .market import fetch_market
from .model import run_model
from .models import Dashboard, Filing, Financials, Resolved
from .reconcile import reconcile
from .render import write_outputs
from .resolve import resolve

STAGES = ("resolve", "fetch", "facts", "extract", "reconcile", "market", "model", "render")


@dataclass
class _Inputs:
    """What the extraction and the model read, which differs between 10-K/10-Q and 20-F filers."""
    financials: Financials
    periodic: Filing  # the filing the instrument list is dated by (a 6-K for a 20-F filer with interim results)
    instance: str
    filing_html: Callable[[], str]
    annual: Filing | None = None
    annual_instance: str | None = None
    debt_filings: list[Filing] = field(default_factory=list)
    debt_texts: dict[str, str] = field(default_factory=dict)
    basis: list[str] = field(default_factory=list)
    settings: dict = field(default_factory=dict)


def build(ticker: str, *, settings: Settings, refresh: bool = False, no_llm: bool = False,
          progress: Callable[[str], None] = lambda s: None, overrides_dir: Path = Path("overrides"),
          model_settings: ModelSettings | None = None, sec_transport: httpx.BaseTransport | None = None,
          llm_transport: httpx.BaseTransport | None = None,
          market_transport: httpx.BaseTransport | None = None) -> Path:
    progress("resolve")
    edgar = Edgar(settings, transport=sec_transport)
    resolved = resolve(ticker, edgar)
    company, filing = resolved.company, resolved.periodic
    ask = partial(llm.ask, transport=llm_transport)

    progress("fetch")
    cache = settings.home / "cache" / str(company.cik) / filing.accession
    max_age = 0 if refresh else None
    companyfacts = edgar.json(companyfacts_url(company.cik), cache / "companyfacts.json", max_age)
    try:
        instance = edgar.text(archive_url(company.cik, filing.accession, instance_doc(filing.primary_doc)),
                              cache / "instance.xml", max_age)
    except SecError:  # older filings without inline XBRL: fall back to the HTML document
        instance = ""
    if filing.form == "20-F":
        inputs = _foreign(resolved, edgar, settings, companyfacts, instance, max_age, no_llm, ask, not refresh, progress)
    else:
        inputs = _domestic(resolved, edgar, settings, companyfacts, instance, max_age, progress)

    progress("extract")
    if no_llm:
        extraction, skipped, sources = None, "--no-llm", {}
    else:
        to_read = resolved.model_copy(update={"periodic": inputs.periodic, "annual": inputs.annual,
                                              "debt_8ks": inputs.debt_filings})
        extraction, skipped, sources = extract(to_read, inputs.instance, inputs.filing_html, inputs.debt_texts,
                                               annual_instance=inputs.annual_instance, ask=ask, settings=settings,
                                               use_cache=not refresh)
    financials = inputs.financials

    progress("reconcile")
    reconciliation, instruments = reconcile(extraction, financials, sources)

    progress("market")
    market = fetch_market(company.ticker, financials.shares_outstanding, overrides_dir, transport=market_transport,
                          cache_dir=settings.home / "cache")

    progress("model")
    model_settings = model_settings or load_model_settings()
    model = run_model(instruments, financials, market, model_settings, undrawn_revolver=reconciliation.undrawn_revolver)
    reconciliation.fit_error = model.fit_error
    risk = creditrisk.risk_view(financials, model, market, model_settings.recovery_rate, use_llm=not no_llm,
                                ask=ask, settings=settings, use_cache=not refresh)
    if not no_llm and risk.implied is not None:  # the model was asked for its read, with this prompt
        inputs.settings["risk_prompt_version"] = creditrisk.PROMPT_VERSION

    progress("render")
    dashboard = Dashboard(
        company=company, filing=inputs.periodic, built_at=datetime.now().replace(microsecond=0),
        financials=financials, extraction=extraction, extraction_skipped=skipped, instruments=instruments,
        reconciliation=reconciliation, market=market, model=model, basis=inputs.basis,
        risk=risk,
        settings=model_settings.model_dump() | {"llm_model": None if no_llm else settings.llm_model,
                                                "prompt_version": None if no_llm else PROMPT_VERSION} | inputs.settings,
    )
    return write_outputs(dashboard, settings.out)


def _domestic(resolved: Resolved, edgar: Edgar, settings: Settings, companyfacts: dict, instance: str,
              max_age: float | None, progress: Callable[[str], None]) -> _Inputs:
    company, filing = resolved.company, resolved.periodic
    cache = settings.home / "cache" / str(company.cik) / filing.accession
    annual_instance = None
    if resolved.annual is not None:  # the 10-K, for the maturity schedule and instrument detail a 10-Q may lack
        a = resolved.annual
        try:
            annual_instance = edgar.text(archive_url(company.cik, a.accession, instance_doc(a.primary_doc)),
                                         settings.home / "cache" / str(company.cik) / a.accession / "instance.xml",
                                         max_age)
        except SecError:
            annual_instance = None
    eight_ks = {f.accession: edgar.text(archive_url(company.cik, f.accession, f.primary_doc),
                                        cache / f"8k-{f.accession}.htm", max_age) for f in resolved.debt_8ks}
    filing_html = partial(edgar.text, archive_url(company.cik, filing.accession, filing.primary_doc),
                          cache / "filing.htm", max_age)
    progress("facts")
    financials = build_financials(companyfacts, instance, filing.period_end, company.fiscal_year_end,
                                  filing.accession, annual_end=resolved.annual.period_end if resolved.annual else None)
    return _Inputs(financials=financials, periodic=filing, instance=instance, filing_html=filing_html,
                   annual=resolved.annual, annual_instance=annual_instance, debt_filings=resolved.debt_8ks,
                   debt_texts=eight_ks)


def _foreign(resolved: Resolved, edgar: Edgar, settings: Settings, companyfacts: dict, instance: str,
             max_age: float | None, no_llm: bool, ask: Callable, use_cache: bool,
             progress: Callable[[str], None]) -> _Inputs:
    """A 20-F filer: the 20-F's full year, rolled forward with the newest 6-K that carries interim statements."""
    company, filing = resolved.company, resolved.periodic
    if not companyfacts.get("facts", {}).get("us-gaap"):
        raise UnsupportedFiler(f"{company.ticker} ({company.name}) files 20-F in IFRS, with no US GAAP data on EDGAR; "
                               "creditdash reads US GAAP XBRL only. Its quarterly results are furnished on 6-K "
                               "without XBRL.")
    fye, cache_root = company.fiscal_year_end, settings.home / "cache"
    annual_fin = build_financials(companyfacts, instance, filing.period_end, fye, filing.accession)
    fy = fiscal_year_of(filing.period_end, fye)
    basis = [f"Full-year figures come from the 20-F for fiscal {fy} (period ended {filing.period_end}, filed "
             f"{filing.filed}). Quarterly results are furnished on 6-K, without XBRL."]
    source = find_interim(edgar, company.cik, resolved.sixks, cache_root)
    stmts, extra = None, {}
    progress("facts")
    if source is None:
        basis.append("No 6-K with interim financial statements was found after the 20-F, so figures are as of the 20-F.")
    elif no_llm:
        basis.append(f"6-K {source.filing.accession} has interim statements; reading them was skipped (--no-llm), "
                     "so figures are as of the 20-F.")
    else:
        stmts, reason, text = extract_interim(source, ask=ask, settings=settings, use_cache=use_cache)
        extra["interim_prompt_version"] = interim.PROMPT_VERSION
        dropped: list[str] = []
        if stmts is not None:
            stmts, dropped = verify(stmts, text)
            if stmts.period_end <= filing.period_end:
                stmts, reason = None, f"its statements end {stmts.period_end}, not after the 20-F"
        if stmts is None:
            basis.append(f"The interim statements in 6-K {source.filing.accession} could not be used ({reason}), "
                         "so figures are as of the 20-F.")
        elif dropped:
            names = ", ".join(d.split(":")[0] for d in dropped)
            basis.append(f"Not confirmed in the 6-K text, so taken from the 20-F or left out: {names}.")
    if stmts is not None:
        base = build_financials(companyfacts, instance, stmts.period_end, fye, filing.accession,
                                annual_end=filing.period_end)
        periodic = Filing(form="6-K", accession=source.filing.accession, filed=source.filing.filed,
                          period_end=stmts.period_end, primary_doc=source.document)
        basis.insert(1, f"Balances, the debt list and last-twelve-month figures are as of {stmts.period_end}, from 6-K "
                        f"{periodic.accession} ({source.document}, unaudited, filed {periodic.filed}), read by the "
                        "model and checked against the filing text.")
        inputs = _Inputs(financials=interim.apply(base, annual_fin, stmts, source, fye), periodic=periodic,
                         instance="", filing_html=lambda: source.html, annual=filing, annual_instance=instance)
        after, exclude = source.filing.filed, {source.filing.accession}
    else:
        cache = cache_root / str(company.cik) / filing.accession
        inputs = _Inputs(financials=annual_fin, periodic=filing, instance=instance,
                         filing_html=partial(edgar.text, archive_url(company.cik, filing.accession, filing.primary_doc),
                                             cache / "filing.htm", max_age))
        after, exclude = filing.period_end, set()
    if not no_llm:
        inputs.debt_filings, inputs.debt_texts = debt_filings(edgar, company.cik, resolved.sixks, after=after,
                                                              exclude=exclude, cache_root=cache_root)
    inputs.basis, inputs.settings = basis, extra
    return inputs
