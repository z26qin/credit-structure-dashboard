"""Debt service, EBITDA scenarios, coverage, cash to raise, the capital stack and the cash waterfall.

Conventions (spec §3.7):
- Monthly simulation from the month after period end. Interest = beginning-of-month balance × rate / 12.
  Bullets repay at the end of their maturity month; amortizing (and unknown) loans pay a period's principal
  evenly over that period's months up to maturity.
- Amortizing loans are fitted so bullets + amortizing match the reported maturity schedule, keeping each
  loan's total equal to its period-end principal. Post-period draws are scheduled on top, unfitted.
- Annual EBITDA, capex and CFO are prorated by months / 12; a consensus figure for the current fiscal
  year is reduced by the quarters already reported in that year.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from .config import ModelSettings
from .facts import bucket_label, fiscal_year_end_date, fiscal_year_of, fiscal_year_start, _months
from .models import (
    SENIORITY_LABELS, CoverageRow, Financials, Instrument, Market, ModelOutput, Period, RaiseRow, ScenarioRow,
    SeniorityClass, ServiceRow, StackClass, WaterfallRow,
)
from .reconcile import maturity_date

BEYOND = "__beyond__"  # principal due after the last modeled period
UNALLOCATED_SOURCE = "reconcile"  # the reported debt no extracted instrument accounts for
GAP_THEREAFTER_YEARS = 5  # its "Thereafter" share is spread over the 5 years after the last dated bucket


@dataclass
class _Loan:
    inst: Instrument
    principal: float  # period-end balance
    post: float
    maturity: date
    rate: float
    flexible: bool  # amortizing or unknown: shape set by fitting
    alloc: dict[str, float] = field(default_factory=dict)  # period label -> period-end principal paid


def build_periods(financials: Financials, last_maturity: date | None, horizon_years: int) -> list[Period]:
    fye = financials.fiscal_year_end
    cal = fye == "1231"
    fy = fiscal_year_of(financials.period_end, fye)
    end_fy = fiscal_year_end_date(fy, fye)
    months_left = _months(financials.period_end, end_fy)
    dated = [b.fiscal_year for b in financials.maturities if b.fiscal_year is not None]
    target = fiscal_year_of(last_maturity, fye) if last_maturity else fy + 5
    last_fy = max(max(dated, default=fy), min(target, fy + horizon_years))
    periods = []
    if 0 < months_left < 12:
        periods.append(Period(label=bucket_label(fy, months_left, cal), fiscal_year=fy, months=months_left,
                              start=financials.period_end + timedelta(days=1), end=end_fy))
    for y in range(fy + 1, last_fy + 1):
        periods.append(Period(label=bucket_label(y, 12, cal), fiscal_year=y, months=12,
                              start=fiscal_year_start(y, fye), end=fiscal_year_end_date(y, fye)))
    return periods


def _month_list(periods: list[Period]) -> list[tuple[date, str]]:
    """(month end, period label) for every month in the modeled window."""
    out = []
    for p in periods:
        d = p.start
        for _ in range(p.months):
            y, m = (d.year, d.month)
            month_end = (date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1))
            out.append((month_end, p.label))
            d = month_end + timedelta(days=1)
    return out


def _rate(i: Instrument, sofr: float | None) -> float | None:
    if i.rate_type == "fixed" and i.fixed_rate_pct is not None:
        return i.fixed_rate_pct
    if i.rate_type == "floating" and i.spread_pct is not None and sofr is not None:
        return sofr + i.spread_pct
    if i.effective_rate_pct is not None:
        return i.effective_rate_pct
    return i.fixed_rate_pct


def run_model(instruments: list[Instrument], financials: Financials, market: Market,
              settings: ModelSettings) -> ModelOutput:
    excluded: list[str] = []
    loans: list[_Loan] = []
    gaps = [i for i in instruments if i.source == UNALLOCATED_SOURCE]
    for i in instruments:
        if i.source == UNALLOCATED_SOURCE:
            continue
        mat = maturity_date(i.maturity)
        balance = (i.principal or 0) + (i.post_period_change or 0)
        if mat is None or (i.principal is None and i.post_period_change is None) or balance <= 0:
            excluded.append(i.name)
            continue
        rate = _rate(i, market.sofr_pct.value)
        if rate is None:
            excluded.append(f"{i.name} (no rate)")
        loans.append(_Loan(inst=i, principal=i.principal or 0, post=i.post_period_change or 0,
                           maturity=max(mat, financials.period_end + timedelta(days=1)), rate=rate or 0.0,
                           flexible=i.amortization != "bullet"))

    gap_total = sum(i.principal or 0 for i in gaps)
    dated = [b.fiscal_year for b in financials.maturities if b.fiscal_year is not None]
    gap_maturity = (fiscal_year_end_date(max(dated) + GAP_THEREAFTER_YEARS, financials.fiscal_year_end)
                    if gap_total > 0 and dated else None)
    last = max([l.maturity for l in loans] + ([gap_maturity] if gap_maturity else []), default=None)
    periods = build_periods(financials, last, settings.horizon_years)
    months = _month_list(periods)
    label_of = _period_of(periods)
    fit_error = _fit(loans, financials, periods, months, label_of, settings.ipf_max_rounds)
    if gap_total > 0:
        rate = _gap_rate(loans, financials)
        if gap_maturity is None or rate is None:
            excluded.append(gaps[0].name + (" (no reported maturity schedule)" if gap_maturity is None else " (no rate)"))
        else:
            gap = _Loan(inst=gaps[0], principal=gap_total, post=0.0, maturity=gap_maturity, rate=rate, flexible=True)
            gap.alloc = _gap_alloc(loans, financials, periods, gap_total, max(dated))
            loans.append(gap)
    service = _simulate(loans, months, label_of)

    scenarios = _scenarios(financials, market, periods, settings)
    due = defaultdict(float)
    for r in service:
        due[r.period] += r.principal + r.interest
    coverage = [CoverageRow(scenario=s.scenario, period=s.period,
                            dscr=s.ebitda / due[s.period] if due[s.period] else None) for s in scenarios]
    return ModelOutput(periods=periods, debt_service=service, scenarios=scenarios, coverage=coverage,
                       raise_rows=_raise(financials, market, periods, service), stack=_stack(instruments, market),
                       waterfall=_waterfall(service, scenarios), excluded=excluded, fit_error=fit_error)


def _period_of(periods: list[Period]):
    def label_of(d: date) -> str:
        for p in periods:
            if p.start <= d <= p.end:
                return p.label
        return BEYOND if periods and d > periods[-1].end else periods[0].label
    return label_of


# --- principal schedule ----------------------------------------------------------------------

def _straight_line(loan: _Loan, months, label_of, amount: float) -> dict[str, float]:
    live = [label for end, label in months if end <= _month_end(loan.maturity)]
    beyond_months = max(0, _months_between(months[-1][0], loan.maturity)) if months else 0
    n = len(live) + beyond_months
    out: dict[str, float] = defaultdict(float)
    if n == 0:
        out[label_of(loan.maturity)] += amount
        return out
    for label in live:
        out[label] += amount / n
    if beyond_months:
        out[BEYOND] += amount * beyond_months / n
    return out


def _month_end(d: date) -> date:
    return date(d.year + d.month // 12, d.month % 12 + 1, 1) - timedelta(days=1)


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month)


def _fit(loans: list[_Loan], financials: Financials, periods, months, label_of, max_rounds: int) -> float | None:
    for loan in loans:
        if loan.flexible:
            loan.alloc = dict(_straight_line(loan, months, label_of, loan.principal))
        else:
            loan.alloc = {label_of(loan.maturity): loan.principal}
    buckets = _buckets(financials, periods)
    if not buckets:
        return None
    error = 0.0
    for _ in range(max_rounds):
        for labels, reported in buckets:
            fixed = sum(v for l in loans if not l.flexible for k, v in l.alloc.items() if k in labels)
            flex = sum(v for l in loans if l.flexible for k, v in l.alloc.items() if k in labels)
            if flex > 0:
                factor = max(reported - fixed, 0.0) / flex
                for l in loans:
                    if l.flexible:
                        for k in labels & l.alloc.keys():
                            l.alloc[k] *= factor
        for l in loans:
            if l.flexible:
                total = sum(l.alloc.values())
                if total > 0:
                    l.alloc = {k: v * l.principal / total for k, v in l.alloc.items()}
                elif l.principal > 0:  # every bucket it spans was already full: repay at maturity
                    l.alloc = {label_of(l.maturity): l.principal}
        error = max(abs(sum(v for l in loans for k, v in l.alloc.items() if k in labels) - reported)
                    for labels, reported in buckets)
        if error < 1e-6:
            break
    return error


def _buckets(financials: Financials, periods: list[Period]) -> list[tuple[set[str], float]]:
    labels = {p.label for p in periods}
    dated = [b for b in financials.maturities if b.fiscal_year is not None and b.label in labels]
    out = [({b.label}, b.principal) for b in dated]
    after = {p.label for p in periods if p.label not in {b.label for b in dated}
             and p.fiscal_year > max((b.fiscal_year for b in dated), default=0)} | {BEYOND}
    out += [(after, b.principal) for b in financials.maturities if b.fiscal_year is None]
    return out


def _gap_rate(loans: list[_Loan], financials: Financials) -> float | None:
    """Balance-weighted rate of the known instruments, else TTM interest ÷ reported debt."""
    priced = [l for l in loans if l.rate > 0 and l.principal > 0]
    if priced:
        return sum(l.rate * l.principal for l in priced) / sum(l.principal for l in priced)
    interest, debt = financials.ttm.interest_expense, financials.total_debt.value
    return interest / debt * 100 if interest and debt else None


def _gap_alloc(loans: list[_Loan], financials: Financials, periods: list[Period], amount: float,
               last_dated_fy: int) -> dict[str, float]:
    """The unallocated debt takes whatever the reported schedule has left after the known instruments."""
    alloc: dict[str, float] = {}
    for labels, reported in _buckets(financials, periods):
        residual = max(reported - sum(v for l in loans for k, v in l.alloc.items() if k in labels), 0.0)
        if BEYOND in labels:  # Thereafter: spread evenly over the following years
            spread = [p.label for p in periods if last_dated_fy < p.fiscal_year <= last_dated_fy + GAP_THEREAFTER_YEARS]
            for label in spread:
                alloc[label] = alloc.get(label, 0.0) + residual / len(spread)
        else:
            for label in labels:
                alloc[label] = alloc.get(label, 0.0) + residual
    total = sum(alloc.values())
    if total <= 0:
        return {periods[-1].label: amount}
    return {k: v * amount / total for k, v in alloc.items()}


def _simulate(loans: list[_Loan], months, label_of) -> list[ServiceRow]:
    rows: dict[tuple[str, str], list[float]] = {}
    for loan in loans:
        payments = [0.0] * len(months)
        by_period: dict[str, list[int]] = defaultdict(list)
        for idx, (end, label) in enumerate(months):
            if end <= _month_end(loan.maturity):
                by_period[label].append(idx)
        mat_idx = next((i for i, (end, _) in enumerate(months) if end == _month_end(loan.maturity)), None)
        for label, amount in loan.alloc.items():
            if label == BEYOND:
                continue
            if loan.flexible and by_period.get(label):
                for idx in by_period[label]:
                    payments[idx] += amount / len(by_period[label])
            elif mat_idx is not None:
                payments[mat_idx] += amount
        if loan.post > 0:
            extra = _straight_line(loan, months, label_of, loan.post) if loan.flexible else {
                label_of(loan.maturity): loan.post}
            for label, amount in extra.items():
                idxs = by_period.get(label) if loan.flexible else ([mat_idx] if mat_idx is not None else [])
                for idx in idxs or []:
                    payments[idx] += amount / len(idxs)
        elif loan.post < 0 and months:
            repay = min(-loan.post, loan.principal)
            scale = (loan.principal - repay) / loan.principal if loan.principal else 0
            payments = [p * scale for p in payments]
            payments[0] += repay
        balance = loan.principal + max(loan.post, 0)
        for idx, (_, label) in enumerate(months):
            interest = balance * loan.rate / 100 / 12
            paid = min(payments[idx], balance)
            balance -= paid
            acc = rows.setdefault((loan.inst.name, label), [0.0, 0.0])
            acc[0] += paid
            acc[1] += interest
    by_name = {l.inst.name: l.inst.seniority_class for l in loans}
    return [ServiceRow(instrument=name, seniority_class=by_name[name], period=label, principal=p, interest=i)
            for (name, label), (p, i) in rows.items() if p > 1e-9 or i > 1e-9]


# --- scenarios, raise, stack, waterfall -------------------------------------------------------

def _annual(financials: Financials, market: Market, field: str, periods: list[Period],
            consensus_only: bool = False) -> dict[str, float] | None:
    """Per-period amounts from consensus (when given) else TTM, prorated; None when neither exists."""
    cons = {c.fiscal_year: getattr(c, field) for c in market.consensus if getattr(c, field) is not None}
    if cons:
        out = {}
        for p in periods:
            fy = min(max(p.fiscal_year, min(cons)), max(cons))
            value = cons[fy]
            if p.months < 12 and p.fiscal_year in cons:
                ytd = [getattr(q, field) for q in financials.quarters
                       if q.end >= fiscal_year_start(p.fiscal_year, financials.fiscal_year_end)
                       and q.end <= financials.period_end]
                value = max(value - sum(v for v in ytd if v is not None), 0.0)
            else:
                value = value * p.months / 12
            out[p.label] = value
        return out
    if consensus_only:
        return None
    ttm = getattr(financials.ttm, field)
    return None if ttm is None else {p.label: ttm * p.months / 12 for p in periods}


def _scenarios(financials: Financials, market: Market, periods, settings: ModelSettings) -> list[ScenarioRow]:
    rows = []
    consensus = _annual(financials, market, "ebitda", periods, consensus_only=True)
    ttm = financials.ttm.ebitda
    series = {"consensus": consensus,
              "ttm": None if ttm is None else {p.label: ttm * p.months / 12 for p in periods}}
    last = financials.quarters[-1].ebitda if financials.quarters else None
    series["run-rate"] = None if last is None else {p.label: last * 4 * p.months / 12 for p in periods}
    base = consensus or series["ttm"]
    if base:  # the haircut starts after the first modeled period
        series["stress"] = {p.label: base[p.label] * (1 if n == 0 else 1 - settings.stress_haircut)
                            for n, p in enumerate(periods)}
    for name, values in series.items():
        if values:
            rows += [ScenarioRow(scenario=name, period=p.label, ebitda=values[p.label]) for p in periods]
    return rows


def _raise(financials: Financials, market: Market, periods, service: list[ServiceRow]) -> list[RaiseRow]:
    capex = _annual(financials, market, "capex", periods)
    cfo = _annual(financials, market, "cfo", periods)
    if capex is None and cfo is None:
        return []
    principal = defaultdict(float)
    for r in service:
        principal[r.period] += r.principal
    rows = []
    for p in periods:
        c, o = (capex or {}).get(p.label, 0.0), (cfo or {}).get(p.label, 0.0)
        rows.append(RaiseRow(period=p.label, capex=c, principal=principal[p.label], cfo=o,
                             to_raise=max(0.0, c + principal[p.label] - o)))
    return rows


def _stack(instruments: list[Instrument], market: Market) -> list[StackClass]:
    amounts: list[tuple[str, float]] = []
    for cls in SeniorityClass:
        amount = sum(max((i.principal or 0) + (i.post_period_change or 0), 0) for i in instruments
                     if i.seniority_class == cls)
        if amount > 0:
            amounts.append((cls.value, amount))
    if market.market_cap.value:
        amounts.append(("equity", market.market_cap.value))
    total = sum(a for _, a in amounts)
    out, junior = [], 1.0
    for cls, amount in amounts:
        share = amount / total if total else 0.0
        junior -= share
        out.append(StackClass(seniority_class=cls, label=SENIORITY_LABELS[cls], amount=amount, share=share,
                              attachment=max(junior, 0.0)))
    return out


def _waterfall(service: list[ServiceRow], scenarios: list[ScenarioRow]) -> list[WaterfallRow]:
    due: dict[tuple[str, SeniorityClass], float] = defaultdict(float)
    for r in service:
        due[(r.period, r.seniority_class)] += r.principal + r.interest
    rows = []
    for s in scenarios:
        remaining = max(s.ebitda, 0.0)
        for cls in SeniorityClass:
            d = due.get((s.period, cls), 0.0)
            if d <= 0:
                continue
            paid = min(d, remaining)
            remaining -= paid
            rows.append(WaterfallRow(period=s.period, scenario=s.scenario, seniority_class=cls, due=d, paid=paid,
                                     shortfall=d - paid))
    return rows
