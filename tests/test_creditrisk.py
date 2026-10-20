from pytest import approx

from creditdash.creditrisk import Assessment, assess, implied, metrics, risk_view
from creditdash.errors import LlmError
from creditdash.models import Fact
from creditdash.model import run_model
from creditdash.render import render
from tests.sample import sample_dashboard
from tests.toy import FIN, MKT, SETTINGS, TOY


def test_cds_implied_probability():
    imp = implied(MKT.model_copy(update={"cds_bps": Fact(value=600)}), 0.4)
    assert imp.basis == "5-year CDS" and imp.hazard == approx(0.10)
    assert imp.pd_1y == approx(0.0952, abs=1e-4) and imp.pd_5y == approx(0.3935, abs=1e-4)


def test_worst_bond_over_sofr():
    imp = implied(MKT.model_copy(update={"bond_yields": {"A": 7.0, "C": 14.0}}), 0.4)  # SOFR 4%
    assert imp.basis.startswith("worst bond: C") and imp.spread_bps == approx(1000)


def test_no_market_input():
    out = run_model(TOY, FIN, MKT, SETTINGS)
    v = risk_view(FIN, out, MKT, 0.4, use_llm=True, ask=None, settings=None)
    assert v.implied is None and "cds_5y_bps" in v.note


def test_metrics_come_from_the_model_headline():
    out = run_model(TOY, FIN, MKT, SETTINGS)
    m = metrics(FIN, out)
    assert m["next12m_interest"] == approx(out.headline.next12m_interest)
    assert m["ebitda_to_next12m_interest"] == approx(500 / out.headline.next12m_interest)
    assert m["liquidity_to_next12m_debt_service"] == approx(
        next(t.value for t in out.headline.tests if t.key == "liquidity_to_next12m_debt_service"))


def fake(result=None, error=None):
    def ask(prompt, system, schema, *, settings, **kw):
        if error:
            raise error
        return result, {}
    return ask


def test_assess_rejects_uncited_figures():
    imp = implied(MKT.model_copy(update={"cds_bps": Fact(value=600)}), 0.4)
    m = {"pd_5y": 0.39, "ebitda_to_next12m_interest": 3.0}
    ok = Assessment(verdict="overreaction", summary="...", cited=["pd_5y", "ebitda_to_next12m_interest"])
    assert assess(imp, m, ask=fake(ok), settings=None)[0] == ok
    bad = Assessment(verdict="fair", summary="...", cited=["made_up_ratio"])
    a, note = assess(imp, m, ask=fake(bad), settings=None)
    assert a is None and "made_up_ratio" in note
    assert assess(imp, m, ask=fake(error=LlmError("x")), settings=None)[1] == "LLM failed: x"


def test_risk_card_is_first_and_highlighted():
    imp = implied(MKT.model_copy(update={"cds_bps": Fact(value=600)}), 0.4)
    view = {"implied": imp.model_dump(), "metrics": {"ebitda_to_next12m_interest": 2.5, "cfo_plus_interest_to_interest": 4.0},
            "assessment": {"verdict": "overreaction", "summary": "Coverage <b>holds</b>.", "cited": ["pd_5y"]}, "note": ""}
    html = render(sample_dashboard().model_copy(update={"risk": view}))
    assert html.index('id="risk"') < html.index('id="kpis"') and 'cw-risk is-overreaction' in html
    assert "39.3%" in html and "Looks like an overreaction" in html and "&lt;b&gt;holds" in html
