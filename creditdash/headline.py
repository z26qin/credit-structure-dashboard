"""Can the company pay its interest over the next twelve months: four coverage tests with a status each, and
the bottom line in words. It reads the model's monthly schedule and the trailing figures; the debt itself is
modeled in model.py.
"""
from __future__ import annotations

from .models import CoverageTest, Financials, Headline, Instrument, Period, RefinancingRow

COVERED, THIN = 2.0, 1.0  # interest covered at least 2x reads as comfortable; under 1x is not covered
LIQUIDITY_COVERED = 1.5
TEST_KEYS = ("ebitda_to_next12m_interest", "cfo_plus_interest_to_interest", "fcf_plus_interest_to_interest",
             "liquidity_to_next12m_debt_service")


def status_for(value: float | None, covered: float = COVERED, thin: float = THIN) -> str:
    if value is None:
        return "n/a"
    return "covered" if value >= covered else "thin" if value >= thin else "not_covered"


def money(m: float | None) -> str:
    if m is None:
        return "—"
    sign, a = ("−" if m < 0 else ""), abs(m)
    return f"{sign}${a / 1000:.1f}B" if a >= 1000 else f"{sign}${a:.0f}M"


def _first_months(values: list[float], n: int = 12) -> float | None:
    return sum(values[:n]) if values else None


def earnings(financials: Financials) -> tuple[float | None, str, str]:
    """The EBITDA the earnings test uses, its name and where it comes from: the latest quarter's adj. EBITDA
    (EBITDA + share-based compensation) × 4, so a fast grower is judged on what it earns now. Without stock
    comp, or without a quarter, it falls back to run-rate EBITDA, then TTM adj. EBITDA, then TTM EBITDA."""
    q, t = (financials.quarters[-1] if financials.quarters else None), financials.ttm
    candidates = []
    if q is not None:
        candidates += [(q.adj_ebitda, 4, "run-rate adj. EBITDA", f"{q.label} × 4"),
                       (q.ebitda, 4, "run-rate EBITDA", f"{q.label} × 4; no stock comp reported")]
    candidates += [(t.adj_ebitda, 1, "adj. EBITDA", t.label), (t.ebitda, 1, "EBITDA", t.label)]
    for value, times, name, source in candidates:
        if value is not None:
            return value * times, name, source
    return None, "EBITDA", "not reported"


def build_headline(financials: Financials, periods: list[Period], month_interest: list[float],
              month_principal: list[float], refinancing: list[RefinancingRow], refi_rate: float | None,
              outstanding: list[Instrument], undrawn_revolver: float | None) -> Headline:
    t = financials.ttm
    i12, p12 = _first_months(month_interest), _first_months(month_principal)
    refi12, left = 0.0, 12
    by_period = {r.period: r.interest for r in refinancing}
    for p in periods:
        if left <= 0:
            break
        take = min(left, p.months)
        refi12 += by_period.get(p.label, 0.0) * take / p.months
        left -= take
    # Liquidity counts revolvers only: a delayed-draw loan's commitment less its balance is mostly principal
    # already repaid, or money tied to specific purchases.
    undrawn = undrawn_revolver if undrawn_revolver is not None else sum(
        max(0.0, i.commitment - (i.principal or 0) - (i.post_period_change or 0)) for i in outstanding
        if i.kind == "revolver" and i.commitment is not None)
    cash = financials.cash.value
    liquidity = None if cash is None else cash + undrawn
    incurred = None if t.interest_expense is None else t.interest_expense + (t.interest_capitalized or 0)
    paid = t.interest_paid if t.interest_paid is not None else t.interest_expense  # added back to CFO
    ratio = lambda a, b: None if a is None or not b else a / b
    cfo_pre = None if t.cfo is None or paid is None else t.cfo + paid
    fcf_pre = None if cfo_pre is None or t.capex is None else cfo_pre - t.capex
    earn, earn_name, earn_source = earnings(financials)
    e, c, f = ratio(earn, i12), ratio(cfo_pre, i12), ratio(fcf_pre, i12)
    liq = ratio(liquidity, (i12 or 0) + (p12 or 0))
    paid_note = "" if t.interest_paid is not None else " (interest expense used for interest paid)"
    tests = [
        CoverageTest(key="ebitda_to_next12m_interest", label="Earnings", value=e, status=status_for(e),
                     detail=f"{earn_name} {money(earn)} ({earn_source}) ÷ next-12-month interest {money(i12)}"),
        CoverageTest(key="cfo_plus_interest_to_interest", label="Operating cash flow", value=c, status=status_for(c),
                     detail=f"operating cash flow before interest {money(cfo_pre)} (TTM){paid_note}"),
        CoverageTest(key="fcf_plus_interest_to_interest", label="After capex", value=f, status=status_for(f),
                     detail=f"the same less capex {money(t.capex)}: {money(fcf_pre)}"),
        CoverageTest(key="liquidity_to_next12m_debt_service", label="Liquidity", value=liq,
                     status=status_for(liq, LIQUIDITY_COVERED),
                     detail=f"cash {money(cash)}" + (f" + undrawn {money(undrawn)}" if undrawn else "") +
                            f" ÷ next-12-month interest and principal {money((i12 or 0) + (p12 or 0))}"),
    ]
    funding, line = bottom_line({x.key: x.status for x in tests})
    return Headline(next12m_interest=i12, next12m_principal=p12, next12m_refinancing_interest=refi12,
                    refinancing_rate_pct=refi_rate, ttm_interest_incurred=incurred, ttm_interest_paid=t.interest_paid,
                    undrawn=undrawn, liquidity=liquidity, earnings=earn,
                    earnings_basis=f"{earn_name} ({earn_source})", tests=tests,
                    funding=funding, bottom_line=line)


def bottom_line(status: dict[str, str]) -> tuple[str | None, str]:
    """The tests in words, from their statuses alone (the numbers sit right above): whether the company needs
    outside money, then why."""
    e, c, f, liq = (status.get(k, "n/a") for k in TEST_KEYS)
    ok = ("covered", "thin")
    parts = []
    if c == "not_covered":
        parts.append("Operating cash flow doesn't cover interest.")
    elif e == "not_covered" and c in ok:
        parts.append("Earnings don't cover interest; operating cash flow does.")
    capex = {"covered": "cash flow covers capex and interest", "thin": "cash flow only just covers capex and interest",
             "not_covered": "cash flow doesn't cover capex and interest"}.get(f)
    cash = {"covered": "liquidity covers next year's interest and principal",
            "thin": "liquidity only just covers next year's interest and principal",
            "not_covered": "liquidity falls short of next year's interest and principal"}.get(liq)
    clauses = [x for x in (capex, cash) if x]
    if clauses:
        joined = "; ".join(clauses)
        parts.append(joined[0].upper() + joined[1:] + ".")
    label = None
    if f != "n/a" and liq != "n/a":
        label = {(True, True): "Self-funding", (True, False): "Pays its way, but must refinance",
                 (False, True): "Burns cash on capex", (False, False): "Needs new capital"}[(f in ok, liq in ok)]
    return label, " ".join(parts)
