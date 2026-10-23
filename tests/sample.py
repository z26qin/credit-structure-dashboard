"""A complete Dashboard for the toy company in tests/toy.py, for render, pipeline and web tests."""
from datetime import date, datetime

from creditdash.model import run_model
from creditdash.models import Company, Dashboard, Extraction, Filing
from creditdash.reconcile import reconcile
from tests.toy import FIN, MKT, SETTINGS, TOY

ACCESSION = "0000000000-26-000001"


def sample_dashboard(ticker: str = "CRWV", instruments=None, extraction_skipped: str | None = None,
                     financials=None) -> Dashboard:
    insts = instruments if instruments is not None else TOY
    fin = financials if financials is not None else FIN
    extraction = None if extraction_skipped else Extraction(period_end=fin.period_end, unit_note="$ millions",
                                                             instruments=insts)
    rec, reconciled = reconcile(extraction, fin, {ACCESSION: " ".join(i.name for i in insts)})
    model = run_model(reconciled, fin, MKT, SETTINGS)
    return Dashboard(
        company=Company(ticker=ticker, cik=1, name="Toy Compute, Inc.", fiscal_year_end="1231"),
        filing=Filing(form="10-Q", accession=ACCESSION, filed=date(2026, 8, 12), period_end=fin.period_end,
                      primary_doc="toy-20260630.htm"),
        built_at=datetime(2026, 10, 20, 12, 0), financials=fin, extraction=extraction,
        extraction_skipped=extraction_skipped, instruments=reconciled, reconciliation=rec, market=MKT, model=model,
        settings=SETTINGS.model_dump() | {"llm_model": "deepseek-flash", "prompt_version": "extract_v1"},
    )
