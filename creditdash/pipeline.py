"""ticker -> resolve -> fetch -> facts -> extract -> reconcile -> market -> model -> render -> index.html"""
from __future__ import annotations

from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Callable

import httpx

from . import llm
from .config import ModelSettings, Settings, load_model_settings
from .edgar import Edgar, archive_url, companyfacts_url, instance_doc
from .errors import SecError
from .extract import PROMPT_VERSION, extract
from .facts import build_financials
from .market import fetch_market
from .model import run_model
from .models import Dashboard
from .reconcile import reconcile
from .render import write_outputs
from .resolve import resolve

STAGES = ("resolve", "fetch", "facts", "extract", "reconcile", "market", "model", "render")


def build(ticker: str, *, settings: Settings, refresh: bool = False, no_llm: bool = False,
          progress: Callable[[str], None] = lambda s: None, overrides_dir: Path = Path("overrides"),
          model_settings: ModelSettings | None = None, sec_transport: httpx.BaseTransport | None = None,
          llm_transport: httpx.BaseTransport | None = None,
          market_transport: httpx.BaseTransport | None = None) -> Path:
    progress("resolve")
    edgar = Edgar(settings, transport=sec_transport)
    resolved = resolve(ticker, edgar)
    company, filing = resolved.company, resolved.periodic

    progress("fetch")
    cache = settings.home / "cache" / str(company.cik) / filing.accession
    max_age = 0 if refresh else None
    companyfacts = edgar.json(companyfacts_url(company.cik), cache / "companyfacts.json", max_age)
    try:
        instance = edgar.text(archive_url(company.cik, filing.accession, instance_doc(filing.primary_doc)),
                              cache / "instance.xml", max_age)
    except SecError:  # older filings without inline XBRL: fall back to the HTML document
        instance = ""
    eight_ks = {f.accession: edgar.text(archive_url(company.cik, f.accession, f.primary_doc),
                                        cache / f"8k-{f.accession}.htm", max_age) for f in resolved.debt_8ks}
    filing_html = partial(edgar.text, archive_url(company.cik, filing.accession, filing.primary_doc),
                          cache / "filing.htm", max_age)

    progress("facts")
    financials = build_financials(companyfacts, instance, filing.period_end, company.fiscal_year_end,
                                  filing.accession)

    progress("extract")
    if no_llm:
        extraction, skipped, sources = None, "--no-llm", {}
    else:
        extraction, skipped, sources = extract(resolved, instance, filing_html, eight_ks,
                                               ask=partial(llm.ask, transport=llm_transport), settings=settings,
                                               use_cache=not refresh)

    progress("reconcile")
    reconciliation, instruments = reconcile(extraction, financials, sources)

    progress("market")
    market = fetch_market(company.ticker, financials.shares_outstanding, overrides_dir, transport=market_transport)

    progress("model")
    model_settings = model_settings or load_model_settings()
    model = run_model(instruments, financials, market, model_settings)
    reconciliation.fit_error = model.fit_error

    progress("render")
    dashboard = Dashboard(
        company=company, filing=filing, built_at=datetime.now().replace(microsecond=0), financials=financials,
        extraction=extraction, extraction_skipped=skipped, instruments=instruments, reconciliation=reconciliation,
        market=market, model=model,
        settings=model_settings.model_dump() | {"llm_model": None if no_llm else settings.llm_model,
                                                "prompt_version": None if no_llm else PROMPT_VERSION},
    )
    return write_outputs(dashboard, settings.out)
