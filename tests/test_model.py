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


def test_null_maturity_follows_the_reported_schedule():
    # Review Focus 1: no crash, not dropped. With no maturity it shares the schedule's residual (here only the
    # Thereafter years are left over) at its own rate, and stays in the stack.
    out = run_model(TOY + [inst("Revolver", 50, maturity=None, seniority="parent_secured", amortization="bullet",
                                fixed=7.0)], FIN, MKT, SETTINGS)
    assert "Revolver" not in out.excluded
    assert sum(r.principal for r in out.debt_service if r.instrument == "Revolver") == approx(50)
    assert any(s.label == "Parent secured" and s.amount == 50 for s in out.stack)


def test_category_totals_follow_the_schedule_by_balance():  # CHTR reports debt only by category
    cats = [inst("Senior unsecured notes", 600, maturity=None, seniority="senior_unsecured", amortization="unknown",
                 rate_type="unknown"),
            inst("Credit facilities", 400, maturity=None, seniority="parent_secured", amortization="unknown",
                 rate_type="floating", spread=1.5)]
    cats[0] = cats[0].model_copy(update={"effective_rate_pct": 5.0})
    out = run_model(cats, FIN, MKT, SETTINGS)
    p = principal_by(out)
    assert p[("Senior unsecured notes", "2027")] == approx(240) and p[("Credit facilities", "2027")] == approx(160)
    assert out.excluded == [] and out.fit_error == approx(0, abs=0.01)  # measured on the final schedule
    i = interest_by(out)  # same schedule shape, so interest per dollar shows the rate: 5.5% (SOFR 4 + 1.5) vs 5%
    assert i[("Credit facilities", "2H26")] / 400 == approx(i[("Senior unsecured notes", "2H26")] / 600 * 5.5 / 5)


def test_null_principal_excluded():
    out = run_model(TOY + [inst("Ghost", None, maturity="2029-01", seniority="other", amortization="bullet",
                                fixed=7.0)], FIN, MKT, SETTINGS)
    assert "Ghost" in out.excluded


def test_no_rate_flagged_but_scheduled():
    out = run_model(TOY + [inst("Mystery", 10, maturity="2027-01", seniority="other", amortization="bullet",
                                rate_type="unknown")], FIN, MKT, SETTINGS)
    assert "Mystery (no rate: interest not modeled)" in out.excluded
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


# --- final review fixes ------------------------------------------------------------------------

def test_matured_before_period_end_is_not_scheduled():
    # ORCL: a 10-K-sourced list kept a note that matured in July for an August period end, and the 10-K's
    # current-year bucket still contained it.
    from datetime import date
    fin = FIN.model_copy(update={"maturities_as_of": date(2025, 12, 31), "maturities": [
        b.model_copy(update={"principal": 400}) if b.label == "2H26" else b for b in FIN.maturities]})
    old = inst("Old notes", 300, maturity="2026-03", seniority="senior_unsecured", amortization="bullet", fixed=2.65)
    out = run_model(TOY + [old], fin, MKT, SETTINGS)
    assert "Old notes (matured before period end)" in out.excluded
    assert not any(r.instrument == "Old notes" for r in out.debt_service)
    assert principal_by(out)[("B", "2H26")] == approx(100, abs=0.01)  # 10-K bucket less the matured note
    assert next(s for s in out.stack if s.label == "Senior unsecured").amount == approx(300)


def test_stress_haircut_starts_the_year_after_a_10k():
    from datetime import date
    from creditdash.models import MaturityBucket
    fin = FIN.model_copy(update={"period_end": date(2025, 12, 31), "maturities": [
        MaturityBucket(label="2026", fiscal_year=2026, months=12, principal=400, tag=None),
        MaturityBucket(label="2027", fiscal_year=2027, months=12, principal=300, tag=None),
        MaturityBucket(label="Thereafter", fiscal_year=None, months=0, principal=300, tag=None)]})
    s = {(r.scenario, r.period): r.ebitda for r in run_model(TOY, fin, MKT, SETTINGS).scenarios}
    assert s[("ttm", "2026")] == approx(500) and s[("stress", "2026")] == approx(350)


def test_raise_needs_capex():
    fin = FIN.model_copy(update={"ttm": FIN.ttm.model_copy(update={"capex": None})})
    assert run_model(TOY, fin, MKT, SETTINGS).raise_rows == []


def test_floating_without_sofr_is_flagged_as_unmodeled_interest():
    out = run_model(TOY, FIN, market(sofr=None), SETTINGS)
    assert "B (no rate: interest not modeled)" in out.excluded


# --- headline coverage, new borrowing ---------------------------------------------------------------

def test_headline_next_12_months_from_the_monthly_schedule(out):
    h = out.headline
    # A 15 (Jul–Jun at 5%), B 10.75 + 8.375 (6% on a balance paying 100 then 100/yr), C 27 (9% all year)
    assert h.next12m_interest == approx(61.125, abs=0.01)
    assert h.next12m_principal == approx(450, abs=0.01)  # A 300 in Jun 2027, B 100 in 2H26 + 50 in 1H27


def test_headline_tests_and_statuses(out):
    t = {x.key: x for x in out.headline.tests}
    assert [x.key for x in out.headline.tests] == [
        "ebitda_to_next12m_interest", "cfo_plus_interest_to_interest", "fcf_plus_interest_to_interest",
        "liquidity_to_next12m_debt_service"]
    assert t["ebitda_to_next12m_interest"].value == approx(500 / 61.125, abs=0.01)
    assert t["ebitda_to_next12m_interest"].status == "covered"
    assert t["cfo_plus_interest_to_interest"].value == approx((240 + 18) / 61.125, abs=0.01)
    assert t["fcf_plus_interest_to_interest"].value == approx((240 + 18 - 200) / 61.125, abs=0.01)
    assert t["fcf_plus_interest_to_interest"].status == "not_covered"
    assert t["liquidity_to_next12m_debt_service"].value == approx(150 / (61.125 + 450), abs=0.01)
    assert out.headline.funding == "Needs new capital"
    assert out.headline.bottom_line == ("Cash flow doesn't cover capex and interest; "
                                        "liquidity falls short of next year's interest and principal.")


def test_undrawn_commitments_count_as_liquidity():
    revolver = inst("Revolver", 20, maturity="2029-11", seniority="parent_secured", amortization="bullet", fixed=7.0)
    revolver = revolver.model_copy(update={"kind": "revolver", "commitment": 100.0})
    h = run_model(TOY + [revolver], FIN, MKT, SETTINGS).headline
    assert (h.undrawn, h.liquidity) == (approx(80), approx(150 + 80))


def test_ttm_interest_incurred_adds_capitalized():
    fin = FIN.model_copy(update={"ttm": FIN.ttm.model_copy(update={"interest_capitalized": 5.0})})
    assert run_model(TOY, fin, MKT, SETTINGS).headline.ttm_interest_incurred == approx(25.0)


def test_refinanced_maturities_keep_paying_interest(out):
    # Maturities of today's debt are refinanced at its average rate, (300×5 + 400×6 + 300×9) / 1000 = 6.6%:
    # a period pays half a period on what it refinances and a full period on what was refinanced before.
    refi = {x.period: x for x in out.refinancing}
    paid = {p: sum(r.principal for r in out.debt_service if r.period == p) for p in ("2H26", "2027")}
    assert refi["2H26"].interest == approx(0.066 * 0.5 * paid["2H26"] / 2)
    assert refi["2027"].interest == approx(0.066 * (paid["2H26"] + paid["2027"] / 2))
    assert refi["2027"].balance == approx(paid["2H26"] + paid["2027"])
    assert out.headline.refinancing_rate_pct == approx(6.6)


def test_cash_to_raise_is_capex_plus_principal_less_cfo(out):
    r = {x.period: x for x in out.raise_rows}["2027"]
    assert r.to_raise == approx(200 + r.principal - 240)


def test_coverage_rows_carry_three_measures(out):
    c = {(x.scenario, x.period): x for x in out.coverage}["ttm", "2027"]
    existing = sum(x.interest for x in out.debt_service if x.period == "2027")
    principal = sum(x.principal for x in out.debt_service if x.period == "2027")
    refi = {x.period: x for x in out.refinancing}["2027"].interest
    assert c.icr == approx(500 / existing) and c.icr_refi == approx(500 / (existing + refi))
    assert c.dscr == approx(500 / (existing + principal))


def test_waterfall_ends_with_refinanced_interest(out):
    rows = [x for x in out.waterfall if (x.period, x.scenario) == ("2027", "ttm")]
    assert rows[-1].seniority_class == "refinanced"
    assert rows[-1].due == approx({x.period: x for x in out.refinancing}["2027"].interest)


def test_refinancing_rate_override():
    s = SETTINGS.model_copy(update={"refinancing_rate_pct": 10.0})
    assert run_model(TOY, FIN, MKT, s).headline.refinancing_rate_pct == 10.0


def test_delayed_draw_commitments_are_not_liquidity():
    # A DDTL's commitment less its balance is mostly principal already repaid, or money tied to specific purchases.
    ddtl = inst("DDTL", 100, maturity="2030-01", seniority="asset_level_secured", amortization="amortizing", fixed=8.0)
    ddtl = ddtl.model_copy(update={"kind": "delayed_draw_term_loan", "commitment": 500.0})
    assert run_model(TOY + [ddtl], FIN, MKT, SETTINGS).headline.undrawn == 0


def test_stated_revolver_availability_wins():
    h = run_model(TOY, FIN, MKT, SETTINGS, undrawn_revolver=300.0).headline
    assert (h.undrawn, h.liquidity) == (300.0, 450.0)
