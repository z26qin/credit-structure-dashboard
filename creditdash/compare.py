"""Two built dashboards side by side: repayment capacity, cash-flow adequacy and cash-flow tightness.

Companies differ in size and fiscal year, so every measure is a ratio or a share of the company's own liquidity or
debt, and every series is dated by period end. Nothing here reads a filing: it all comes from the two Dashboards.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime

from .headline import TEST_KEYS, money
from .model import MATURED, NO_RATE, UNALLOCATED_SOURCE
from .models import Comparison, Dashboard, Measure, PathPoint, QuarterPoint, Side

EVEN = 0.05  # values within 5% of each other are even
PERIOD_GAP_DAYS = 45  # period ends further apart than this: the trailing windows differ
SCENARIO_ORDER = ("consensus", "ttm", "run-rate", "stress")
NEVER = "not within the modeled years"  # a runway that outlasts the model: the strongest value
NET_CASH = "net cash"  # more cash than debt: the strongest value of a leverage measure
SECTIONS = ("repayment", "adequacy", "tightness")

_ratio = lambda a, b: None if a is None or b is None or b == 0 else a / b


def _balance(i) -> float:
    return (i.principal or 0) + (i.post_period_change or 0)


def _scheduled(i) -> float:
    """What the model schedules: period-end principal plus post-period draws. A post-period repurchase is paid in
    the first modeled month, so it stays in."""
    return (i.principal or 0) + max(i.post_period_change or 0, 0)


def _outstanding(d: Dashboard) -> list:
    matured = {e.removesuffix(" " + MATURED) for e in d.model.excluded if e.endswith(MATURED)}
    return [i for i in d.instruments if i.name not in matured and _balance(i) > 0]


def _runway(liquidity: float | None, flows: list[tuple[int, float]]) -> tuple[float | None, str]:
    """Months until liquidity plus the cumulative flows falls below zero, linear within each period."""
    if liquidity is None:
        return None, "no liquidity figure"
    if liquidity <= 0:
        return 0.0, ""
    if not flows:
        return None, "no capex reported"
    left, elapsed = liquidity, 0
    for months, flow in flows:
        if left + flow < 0:
            return elapsed + months * left / -flow, ""
        left += flow
        elapsed += months
    return None, NEVER


def _path(d: Dashboard) -> tuple[list[PathPoint], list[tuple[int, float]], list[tuple[int, float]]]:
    """Per period: coverage by scenario, liquidity left as a share of today's, and debt due so far as a share of
    today's debt. Also the flows (months, CFO − capex − principal or − refinancing interest) the runway reads."""
    m, h = d.model, d.model.headline
    coverage = {(r.scenario, r.period): r for r in m.coverage}
    scenarios = {r.scenario for r in m.coverage}
    raise_rows = {r.period: r for r in m.raise_rows}
    refi = {r.period: r.interest for r in m.refinancing}
    principal: dict[str, float] = defaultdict(float)
    for r in m.debt_service:
        principal[r.period] += r.principal
    debt = sum(_scheduled(i) for i in _outstanding(d)) or None
    liquidity = h.liquidity if h is not None else None
    base = liquidity if liquidity and liquidity > 0 else None
    paid_flows, refi_flows = [], []
    left_paid = left_refi = liquidity or 0.0
    due = 0.0
    points = []
    for p in m.periods:
        r = raise_rows.get(p.label)
        if r is not None:
            paid_flows.append((p.months, r.cfo - r.capex - r.principal))
            refi_flows.append((p.months, r.cfo - r.capex - refi.get(p.label, 0.0)))
            left_paid += paid_flows[-1][1]
            left_refi += refi_flows[-1][1]
        due += principal[p.label]
        points.append(PathPoint(
            period=p.label, end=p.end, months=p.months,
            icr_refi={s: coverage[(s, p.label)].icr_refi for s in scenarios if (s, p.label) in coverage},
            dscr={s: coverage[(s, p.label)].dscr for s in scenarios if (s, p.label) in coverage},
            liquidity_paid=_ratio(left_paid, base) if r is not None else None,
            liquidity_refinanced=_ratio(left_refi, base) if r is not None else None,
            due_cumulative=_ratio(due, debt)))
    return points, paid_flows, refi_flows


def _quarters(d: Dashboard) -> list[QuarterPoint]:
    out = []
    for q in d.financials.quarters:
        paid = q.interest_paid if q.interest_paid else q.interest_expense
        cover = None if q.cfo is None or not paid else (q.cfo + paid) / paid
        fcf = None if q.cfo is None or q.capex is None else q.cfo - q.capex
        out.append(QuarterPoint(label=q.label, end=q.end, cash_interest_cover=cover,
                                fcf_margin=_ratio(fcf, q.revenue) if q.revenue and q.revenue > 0 else None))
    return out


def side(d: Dashboard) -> tuple[Side, dict[str, tuple[float | None, str]]]:
    """The page's column for one company, and its measures as key -> (value, note)."""
    fin, t, h = d.financials, d.financials.ttm, d.model.headline
    path, paid_flows, refi_flows = _path(d)
    debt, cash = fin.total_debt.value, fin.cash.value
    net = None if debt is None or cash is None else debt - cash
    fcf = None if t.cfo is None or t.capex is None else t.cfo - t.capex
    p12 = h.next12m_principal if h is not None else None
    liquidity = h.liquidity if h is not None else None
    v: dict[str, tuple[float | None, str]] = {}

    tests = {x.key: x for x in h.tests} if h is not None else {}
    for key in TEST_KEYS:
        v[key] = (tests[key].value, "") if key in tests else (None, "no headline in this build")
    if net is not None and net <= 0:
        v["net_debt_to_ebitda"] = (None, NET_CASH)
    elif t.ebitda is None or net is None:
        v["net_debt_to_ebitda"] = (None, "no EBITDA" if t.ebitda is None else "no debt or cash figure")
    else:
        v["net_debt_to_ebitda"] = (net / t.ebitda, "") if t.ebitda > 0 else (None, "EBITDA negative")
    if net is not None and net <= 0:
        v["years_to_repay"] = (None, NET_CASH)
    elif fcf is None or net is None:
        v["years_to_repay"] = (None, "no capex reported" if t.capex is None else "no cash flow figure")
    else:
        v["years_to_repay"] = (net / fcf, "") if fcf > 0 else (None, "free cash flow negative")

    needs = None if t.capex is None or p12 is None else t.capex + p12
    v["adequacy"] = (_ratio(t.cfo, needs), "" if needs else "no capex reported" if t.capex is None else "nothing to fund")
    v["cfo_to_debt"] = (_ratio(t.cfo, debt), "")
    v["fcf_to_debt"] = (_ratio(fcf, debt), "" if fcf is not None else "no capex reported")
    margin = _ratio(fcf, t.revenue) if t.revenue and t.revenue > 0 else None
    v["fcf_margin"] = (margin, "" if margin is not None else "no capex reported" if t.capex is None else "no revenue")
    capex_cfo = _ratio(t.capex, t.cfo) if t.cfo and t.cfo > 0 else None
    v["capex_to_cfo"] = (capex_cfo, "" if capex_cfo is not None else
                         "no capex reported" if t.capex is None else "operating cash flow negative")

    v["runway_paid"] = _runway(liquidity, paid_flows)
    v["runway_refinanced"] = _runway(liquidity, refi_flows)
    outstanding = _outstanding(d)
    scheduled = sum(_scheduled(i) for i in outstanding)
    v["due_12m"] = (_ratio(p12, scheduled) if scheduled else None, "" if scheduled else "no debt")
    total = sum(_balance(i) for i in outstanding)
    if d.extraction_skipped:
        v["floating_share"] = (None, "no instrument list")
    else:
        floating = sum(_balance(i) for i in outstanding if i.rate_type == "floating")
        v["floating_share"] = (_ratio(floating, total) if total else None, "" if total else "no debt")

    for key, value in (("revenue", t.revenue), ("ebitda", t.ebitda), ("total_debt", debt), ("cash", cash),
                       ("liquidity", liquidity), ("market_cap", d.market.market_cap.value)):
        v[key] = (value, "")
    implied = d.risk.implied if d.risk is not None else None
    v["pd_1y"] = (implied.pd_1y, "") if implied else (None, "no CDS or bond yield given")
    v["spread_bps"] = (implied.spread_bps, implied.basis) if implied else (None, "no CDS or bond yield given")

    notes = list(d.basis)
    if d.extraction_skipped:
        notes.append(f"No instrument list ({d.extraction_skipped}): the debt profile rests on SEC totals.")
    if d.reconciliation.status.value == "fail":
        notes.append("Instruments do not reconcile to the reported debt (gap over 5%).")
    no_rate = [e.removesuffix(" " + NO_RATE) for e in d.model.excluded if e.endswith(NO_RATE)]
    if no_rate:
        notes.append(f"Interest not modeled for {', '.join(no_rate)}: coverage is overstated.")
    if any(i.source == UNALLOCATED_SOURCE for i in outstanding) and not d.extraction_skipped:
        gap = sum(_balance(i) for i in outstanding if i.source == UNALLOCATED_SOURCE)
        notes.append(f"{money(gap)} of reported debt has no instrument (Unallocated).")

    s = Side(ticker=d.company.ticker, name=d.company.name, cik=d.company.cik, form=d.filing.form,
             accession=d.filing.accession, primary_doc=d.filing.primary_doc, filed=d.filing.filed,
             period_end=fin.period_end, built_at=d.built_at, reconciliation=d.reconciliation.status.value,
             funding=h.funding if h is not None else None, bottom_line=h.bottom_line if h is not None else "",
             tests=h.tests if h is not None else [], liquidity=liquidity, cfo=t.cfo, capex=t.capex,
             next12m_principal=p12, path=path, quarters=_quarters(d), notes=notes)
    return s, v


# key, section, label, unit, better, how it is computed
MEASURES: list[tuple[str, str, str, str, str | None, str]] = [
    ("ebitda_to_next12m_interest", "repayment", "Earnings ÷ interest", "x", "higher",
     "TTM EBITDA ÷ interest on today's debt over the next 12 months"),
    ("cfo_plus_interest_to_interest", "repayment", "Operating cash flow ÷ interest", "x", "higher",
     "TTM operating cash flow with interest paid added back ÷ next-12-month interest"),
    ("fcf_plus_interest_to_interest", "repayment", "After capex ÷ interest", "x", "higher",
     "the same less TTM capex ÷ next-12-month interest"),
    ("liquidity_to_next12m_debt_service", "repayment", "Liquidity ÷ debt service", "x", "higher",
     "cash plus undrawn revolvers ÷ next-12-month interest and principal"),
    ("net_debt_to_ebitda", "repayment", "Net debt ÷ EBITDA", "x", "lower", "(reported debt − cash) ÷ TTM EBITDA"),
    ("years_to_repay", "repayment", "Years to repay net debt", "years", "lower",
     "net debt ÷ TTM free cash flow (operating cash flow − capex)"),
    ("adequacy", "adequacy", "Cash-flow adequacy", "pct", "higher",
     "TTM operating cash flow ÷ (TTM capex + principal due in the next 12 months)"),
    ("cfo_to_debt", "adequacy", "Operating cash flow ÷ debt", "pct", "higher", "TTM operating cash flow ÷ reported debt"),
    ("fcf_to_debt", "adequacy", "Free cash flow ÷ debt", "pct", "higher", "TTM (operating cash flow − capex) ÷ reported debt"),
    ("fcf_margin", "adequacy", "Free cash flow margin", "pct", "higher", "TTM (operating cash flow − capex) ÷ revenue"),
    ("capex_to_cfo", "adequacy", "Capex ÷ operating cash flow", "x", "lower", "TTM capex ÷ TTM operating cash flow"),
    ("runway_paid", "tightness", "Runway, maturities paid", "months", "higher",
     "months until cash plus undrawn revolvers runs out with no new money: forward CFO − capex − scheduled principal"),
    ("runway_refinanced", "tightness", "Runway, maturities refinanced", "months", "higher",
     "the same with maturities refinanced at the assumed rate, so only their interest is paid"),
    ("due_12m", "tightness", "Principal due in 12 months", "pct", "lower",
     "principal due in the next 12 months ÷ period-end debt plus post-period draws"),
    ("floating_share", "tightness", "Floating-rate share", "pct", "lower",
     "floating-rate balance ÷ today's debt; debt with an unknown rate type counts as fixed"),
    ("revenue", "context", "Revenue, TTM", "usd", None, "trailing twelve months"),
    ("ebitda", "context", "EBITDA, TTM", "usd", None, "operating income + D&A, trailing twelve months"),
    ("total_debt", "context", "Total debt", "usd", None, "principal at period end, as reported"),
    ("cash", "context", "Cash", "usd", None, "including restricted cash where reported"),
    ("liquidity", "context", "Liquidity", "usd", None, "cash plus committed, undrawn revolvers"),
    ("market_cap", "context", "Market cap", "usd", None, "share price × shares outstanding"),
    ("pd_1y", "market", "Implied default, 1 year", "pct", "lower",
     "default probability a 5-year CDS or the worst bond's spread implies (flat hazard rate)"),
    ("spread_bps", "market", "Spread", "bps", "lower", "5-year CDS, else the worst bond's yield over SOFR"),
]


def _rank(value: float | None, note: str, better: str) -> float | None:
    if value is not None:
        return value
    return (math.inf if better == "higher" else -math.inf) if note in (NEVER, NET_CASH) else None


def stronger(value_a: float | None, note_a: str, value_b: float | None, note_b: str,
             better: str | None) -> str | None:
    """"a", "b" or "even" by the measure's direction; None when either side has no value."""
    if better is None:
        return None
    a, b = _rank(value_a, note_a, better), _rank(value_b, note_b, better)
    if a is None or b is None:
        return None
    if a == b or (math.isfinite(a) and math.isfinite(b) and abs(a - b) <= EVEN * max(abs(a), abs(b))):
        return "even"
    return "a" if (a > b) == (better == "higher") else "b"


def compare(a: Dashboard, b: Dashboard) -> Comparison:
    side_a, va = side(a)
    side_b, vb = side(b)
    any_market = va["pd_1y"][0] is not None or vb["pd_1y"][0] is not None
    measures = []
    for key, section, label, unit, better, detail in MEASURES:
        if section == "market" and not any_market:
            continue
        (xa, na), (xb, nb) = va[key], vb[key]
        measures.append(Measure(key=key, section=section, label=label, unit=unit, better=better, a=xa, b=xb,
                                a_note=na, b_note=nb, stronger=stronger(xa, na, xb, nb, better), detail=detail))
    tally = {s: {"a": 0, "b": 0, "even": 0} for s in SECTIONS}
    for m in measures:
        if m.section in tally and m.stronger:
            tally[m.section][m.stronger] += 1
    have = lambda d: {r.scenario for r in d.model.scenarios}
    shared = [s for s in SCENARIO_ORDER if s in have(a) & have(b)]
    notes = []
    gap = abs((side_a.period_end - side_b.period_end).days)
    if gap > PERIOD_GAP_DAYS:
        notes.append(f"Period ends differ by {gap} days ({side_a.ticker} {side_a.period_end}, {side_b.ticker} "
                     f"{side_b.period_end}): the trailing twelve months cover different windows.")
    if side_a.ticker == side_b.ticker:
        notes.append("Both columns are the same company.")
    return Comparison(built_at=datetime.now().replace(microsecond=0), a=side_a, b=side_b, scenarios=shared,
                      measures=measures, tally=tally, notes=notes)


def _r(v: float | None, unit: str) -> float | None:
    if v is None:
        return None
    return round(v, 1) if unit in ("usd", "months", "bps") else round(v, 3) if unit == "pct" else round(v, 2)


def summary(c: Comparison) -> dict:
    """The comparison for a model to read: verdicts, the measures with the stronger side named, and the tallies.
    Money is $ millions; "pct" measures are fractions (0.25 = 25%)."""
    name = {"a": c.a.ticker, "b": c.b.ticker, "even": "even"}
    company = lambda s: {
        "ticker": s.ticker, "name": s.name, "form": s.form, "period_end": s.period_end.isoformat(),
        "funding": s.funding, "bottom_line": s.bottom_line, "reconciliation": s.reconciliation,
        "tests": [{"label": t.label, "value": _r(t.value, "x"), "status": t.status} for t in s.tests],
        "notes": s.notes,
    }
    out = {
        "units": "$ millions; 'pct' measures are fractions (0.25 = 25%); 'x' are multiples",
        "companies": [company(c.a), company(c.b)],
        "tally": {sec: {name[k]: n for k, n in t.items()} for sec, t in c.tally.items()},
        "measures": [
            {"section": m.section, "label": m.label, "unit": m.unit, "better": m.better,
             c.a.ticker: _r(m.a, m.unit), c.b.ticker: _r(m.b, m.unit),
             **({f"{c.a.ticker}_note": m.a_note} if m.a_note else {}),
             **({f"{c.b.ticker}_note": m.b_note} if m.b_note else {}),
             "stronger": name.get(m.stronger) if m.stronger else None, "how": m.detail}
            for m in c.measures
        ],
        "shared_scenarios": c.scenarios,
    }
    if c.notes:
        out["notes"] = c.notes
    return out
