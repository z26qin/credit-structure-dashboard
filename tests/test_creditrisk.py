from pytest import approx

from creditdash.creditrisk import Draft, assess, implied, metrics, risk_view
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


IMP = implied(MKT.model_copy(update={"cds_bps": Fact(value=600)}), 0.4)  # 9.5% in one year
METS = {"ttm_fcf": -13655.0, "ebitda_to_next12m_interest": 1.4532, "next12m_principal": None}


def test_model_read_numbers_are_filled_in_by_code():
    d = Draft(verdict="fair", reasons=["A {pd_1y} one-year default chance fits free cash flow of {ttm_fcf}.",
                                       "Earnings cover interest only {ebitda_to_next12m_interest}."],
              watch="Refinancing over the next 12 months.")
    a, note = assess(IMP, METS, ask=fake(d), settings=None)
    assert a.reasons == ["A 9.5% one-year default chance fits free cash flow of −$13.7B.",
                         "Earnings cover interest only 1.5x."]
    assert a.watch == "Refinancing over the next 12 months." and note == ""
    assert a.cited == ["pd_1y", "ttm_fcf", "ebitda_to_next12m_interest"]


def test_model_read_with_its_own_numbers_is_sent_back_once():
    prompts = []
    replies = [Draft(verdict="fair", reasons=["Coverage is 1.45x."]),
               Draft(verdict="fair", reasons=["Coverage is {ebitda_to_next12m_interest}."])]
    def ask(prompt, system, schema, *, settings, **kw):
        prompts.append(prompt)
        return replies[len(prompts) - 1], {}
    a, _ = assess(IMP, METS, ask=ask, settings=None)
    assert a.reasons == ["Coverage is 1.5x."] and "1.45x" in prompts[1] and "1.45x" not in prompts[0]


def test_model_read_is_left_out_when_it_cites_unknown_or_missing_figures():
    bad = Draft(verdict="fair", reasons=["Debt is {made_up_ratio}, principal {next12m_principal}."])
    a, note = assess(IMP, METS, ask=fake(bad), settings=None)
    assert a is None and "made_up_ratio" in note and "next12m_principal" in note


def test_model_read_may_not_add_its_own_unit_or_sign():
    for text in ("Earnings cover interest {ebitda_to_next12m_interest} times.", "Negative {ttm_fcf} of free cash flow."):
        a, note = assess(IMP, METS, ask=fake(Draft(verdict="fair", reasons=[text])), settings=None)
        assert a is None and "already" in note


def test_model_sees_each_figure_as_the_page_shows_it():
    seen = []
    def ask(prompt, system, schema, *, settings, **kw):
        seen.append(prompt)
        return Draft(verdict="fair", reasons=["Fits {ttm_fcf}."]), {}
    assess(IMP, METS, ask=ask, settings=None)
    assert '"ttm_fcf": "\u2212$13.7B"' in seen[0] and '"pd_1y": "9.5%"' in seen[0] and "next12m_principal" in seen[0]
    assert assess(IMP, METS, ask=fake(error=LlmError("x")), settings=None)[1] == "LLM failed: x"


def test_risk_card_is_first_and_highlighted():
    imp = implied(MKT.model_copy(update={"cds_bps": Fact(value=600)}), 0.4)
    view = {"implied": imp.model_dump(), "metrics": {"ebitda_to_next12m_interest": 2.5, "cfo_plus_interest_to_interest": 4.0},
            "assessment": {"verdict": "overreaction", "reasons": ["Coverage <b>holds</b>.", "Liquidity is ample."],
                           "watch": "The next refinancing.", "cited": ["pd_5y"]}, "note": ""}
    html = render(sample_dashboard().model_copy(update={"risk": view}))
    assert html.index('id="risk"') < html.index('id="kpis"') and 'cw-risk is-overreaction' in html
    assert "39.3%" in html and "Looks like an overreaction" in html and "&lt;b&gt;holds" in html
    assert html.count("<li>", html.index('id="risk"'), html.index('id="kpis"')) == 2 and "The next refinancing." in html
