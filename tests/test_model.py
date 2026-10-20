import pytest
from pytest import approx

from creditdash.model import run_model
from creditdash.models import ConsensusYear, SeniorityClass
from creditdash.reconcile import unallocated
from tests.toy import FIN, MKT, SETTINGS, TOY, financials, inst, market


@pytest.fixture(scope="module")
def out():
    return run_model(TOY, FIN, MKT, SETTINGS)


def principal_by(out):
    return {(r.instrument, r.period): r.principal for r in out.debt_service}


def interest_by(out):
    return {(r.instrument, r.period): r.interest for r in out.debt_service}


def test_periods(out):
    assert [p.label for p in out.periods] == ["2H26", "2027", "2028", "2029", "2030", "2031"]
    assert (out.periods[0].months, out.periods[1].months) == (6, 12)


def test_principal_schedule(out):
    p = principal_by(out)
    assert p[("A", "2027")] == approx(300)
    assert [p[("B", k)] for k in ("2H26", "2027", "2028")] == approx([100, 100, 200], abs=0.01)
    assert p[("C", "2031")] == approx(300) and out.fit_error < 0.01


def test_interest(out):
    i = interest_by(out)
    assert i[("A", "2H26")] == approx(7.5) and i[("A", "2027")] == approx(7.5)
    assert ("A", "2028") not in i
    assert i[("B", "2027")] == approx(15.25, abs=0.01)  # 6% on Jan–Dec beginning balances 300 → 208.33
    assert i[("C", "2027")] == approx(27.0)


def test_stack_attachments(out):
    assert [(s.label, round(s.attachment, 2)) for s in out.stack] == [
        ("Asset-level secured", 0.80), ("Senior unsecured", 0.65), ("Convertible", 0.50), ("Equity", 0.0)]
    assert sum(s.share for s in out.stack) == approx(1.0)


def test_stack_without_market_cap():
    out = run_model(TOY, FIN, market(market_cap=None), SETTINGS)
    assert [s.label for s in out.stack] == ["Asset-level secured", "Senior unsecured", "Convertible"]
    assert out.stack[0].attachment == approx(0.6)


def test_null_maturity_excluded():
    out = run_model(TOY + [inst("Revolver", 50, maturity=None, seniority="parent_secured", amortization="bullet",
                                fixed=7.0)], FIN, MKT, SETTINGS)
    assert "Revolver" in out.excluded and not any(r.instrument == "Revolver" for r in out.debt_service)
    assert any(s.label == "Parent secured" and s.amount == 50 for s in out.stack)


def test_null_principal_excluded():
    out = run_model(TOY + [inst("Ghost", None, maturity="2029-01", seniority="other", amortization="bullet",
                                fixed=7.0)], FIN, MKT, SETTINGS)
    assert "Ghost" in out.excluded


def test_no_rate_flagged_but_scheduled():
    out = run_model(TOY + [inst("Mystery", 10, maturity="2027-01", seniority="other", amortization="bullet",
                                rate_type="unknown")], FIN, MKT, SETTINGS)
    assert "Mystery (no rate)" in out.excluded
    assert principal_by(out)[("Mystery", "2027")] == approx(10)


def test_post_period_draw_scheduled_on_top():
    toy = [i.model_copy(update={"post_period_change": 100.0}) if i.name == "C" else i for i in TOY]
    out = run_model(toy, FIN, MKT, SETTINGS)
    assert principal_by(out)[("C", "2031")] == approx(400)
    assert next(s for s in out.stack if s.label == "Convertible").amount == approx(400)


def test_scenarios(out):
    s = {(r.scenario, r.period): r.ebitda for r in out.scenarios}
    assert s[("ttm", "2H26")] == approx(250) and s[("ttm", "2027")] == approx(500)
    assert s[("run-rate", "2027")] == approx(600)
    assert s[("stress", "2H26")] == approx(250) and s[("stress", "2027")] == approx(350)
    assert not any(k[0] == "consensus" for k in s)


def test_consensus_scenario():
    mkt = market().model_copy(update={"consensus": [ConsensusYear(fiscal_year=2026, ebitda=700, capex=100, cfo=80),
                                                    ConsensusYear(fiscal_year=2027, ebitda=900)]})
    out = run_model(TOY, FIN, mkt, SETTINGS)
    s = {(r.scenario, r.period): r.ebitda for r in out.scenarios}
    assert s[("consensus", "2H26")] == approx(700 - 150)  # FY26 consensus less the reported Q2 26 quarter
    assert s[("consensus", "2027")] == approx(900) and s[("consensus", "2030")] == approx(900)
    assert s[("stress", "2027")] == approx(900 * 0.7)


def test_dscr_and_raise(out):
    c = {(r.scenario, r.period): r.dscr for r in out.coverage}
    due = sum(r.interest + r.principal for r in out.debt_service if r.period == "2027")
    assert c[("ttm", "2027")] == approx(FIN.ttm.ebitda / due)
    r = {x.period: x for x in out.raise_rows}
    assert r["2027"].to_raise == approx(max(0, 200 + 400 - 240))
    assert all(x.to_raise >= 0 for x in out.raise_rows)


def test_unallocated_follows_reported_schedule():
    # Totals-only build (no extraction): the whole debt follows the reported schedule; Thereafter is spread
    # evenly over the five years after the last dated bucket (2029–2033); interest at TTM interest ÷ debt (2%).
    out = run_model([unallocated(1000.0)], FIN, MKT, SETTINGS)
    p = principal_by(out)
    assert [p[("Unallocated", k)] for k in ("2H26", "2027", "2028")] == approx([100, 400, 200])
    assert [p[("Unallocated", str(y))] for y in range(2029, 2034)] == approx([60] * 5)
    assert "Unallocated" not in out.excluded
    assert interest_by(out)[("Unallocated", "2H26")] == approx(1000 * 0.02 / 2 - 0.02 / 12 * 100 / 6 * 15, abs=0.01)
    assert {c.period for c in out.coverage} >= {"2027", "2033"}


def test_unallocated_takes_only_the_residual():
    out = run_model(TOY[:2] + [unallocated(300.0)], FIN, MKT, SETTINGS)
    p = principal_by(out)
    assert sum(v for (n, _), v in p.items() if n == "Unallocated") == approx(300)
    assert p.get(("Unallocated", "2027"), 0) == approx(0, abs=1e-6)
    assert p[("B", "2027")] == approx(100, abs=0.01)


def test_waterfall_shortfall():  # TTM EBITDA 150: B due 115.25 is paid, A due 307.5 is short
    out = run_model(TOY, financials(ttm_ebitda=150), MKT, SETTINGS)
    w = {r.seniority_class: r for r in out.waterfall if (r.period, r.scenario) == ("2027", "ttm")}
    assert w[SeniorityClass.asset_level_secured].shortfall == 0
    assert w[SeniorityClass.senior_unsecured].shortfall == approx(307.5 - (150 - 115.25), abs=0.01)
    assert w[SeniorityClass.convertible].paid == 0
