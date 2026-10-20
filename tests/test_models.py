import json
from datetime import date, datetime

from creditdash.models import (
    Company, Dashboard, Extraction, Fact, Filing, Financials, Instrument, Market, ModelOutput,
    Quarter, Reconciliation, ReconStatus, SeniorityClass,
)


def quarter(**kw) -> Quarter:
    base = dict(label="Q2 26", start=date(2026, 4, 1), end=date(2026, 6, 30), revenue=2575.0,
                operating_income=-49.0, da=1393.0, interest_expense=558.0, interest_paid=None,
                cfo=679.0, capex=None)
    return Quarter(**(base | kw))


def test_seniority_order():
    assert list(SeniorityClass)[0] == SeniorityClass.asset_level_secured
    assert list(SeniorityClass)[-1] == SeniorityClass.other


def test_quarter_ebitda():
    assert quarter().ebitda == 1344.0
    assert quarter(da=None).ebitda is None


def test_llm_schema_has_no_verified():
    assert "verified" not in json.dumps(Extraction.llm_schema())
    assert "quotes" in json.dumps(Extraction.llm_schema())


def test_dashboard_roundtrip():
    q = quarter()
    fin = Financials(period_end=date(2026, 6, 30), fiscal_year_end="1231", total_debt=Fact(value=35551.0),
                     cash=Fact(value=6904.0), op_lease=Fact(value=None), fin_lease=Fact(value=None),
                     rpo=Fact(value=None), shares_outstanding=Fact(value=None), maturities=[],
                     quarters=[q], ttm=q.model_copy(update={"label": "TTM"}))
    inst = Instrument(name="DDTL 4.0 Facility", kind="delayed_draw_term_loan",
                      seniority_class="asset_level_secured", principal=2837.0, rate_type="floating",
                      spread_pct=2.25, benchmark="SOFR", maturity="2032-03", amortization="amortizing",
                      source="0001769628-26-000366", quotes=["DDTL 4.0 Facility"], verified=False)
    d = Dashboard(
        company=Company(ticker="CRWV", cik=1769628, name="CoreWeave, Inc.", fiscal_year_end="1231"),
        filing=Filing(form="10-Q", accession="0001769628-26-000366", filed=date(2026, 8, 12),
                      period_end=date(2026, 6, 30), primary_doc="crwv-20260630.htm"),
        built_at=datetime(2026, 10, 20, 12, 0), financials=fin,
        extraction=Extraction(period_end=date(2026, 6, 30), unit_note="$ millions", instruments=[inst]),
        extraction_skipped=None, instruments=[inst],
        reconciliation=Reconciliation(status=ReconStatus.pass_, reported_total=35551.0),
        market=Market(sofr_pct=Fact(value=3.87), price=Fact(value=None), market_cap=Fact(value=None)),
        model=ModelOutput(periods=[], debt_service=[], scenarios=[], coverage=[], raise_rows=[], stack=[],
                          waterfall=[], excluded=[], fit_error=None),
        settings={"stress_haircut": 0.3},
    )
    back = Dashboard.model_validate_json(d.model_dump_json())
    assert back == d
    assert back.instruments[0].verified is False


def test_maturity_normalized():
    from creditdash.models import Instrument
    cases = [("2030-12-31", "2030-12"), ("2030", "2030-12"), ("Dec 2030", "2030-12"), ("December 2030", "2030-12"),
             ("2030-06", "2030-06"), ("Various", None), (None, None)]
    for raw, want in cases:
        assert Instrument(name="x", kind="other", seniority_class="other", source="s", maturity=raw).maturity == want, raw
