"""Reported numbers from SEC XBRL: balances at period end, the maturity schedule, quarterly series.

Reads companyfacts (non-dimensional facts, all filings) and, for shares outstanding, the filing's own
instance. Values are converted to $ millions; shares stay a count.
"""
from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from .models import Fact, Financials, MaturityBucket, Quarter

TOLERANCE_DAYS = 7  # 52/53-week fiscal years end on slightly different dates each year
MATURITY = "LongTermDebtMaturitiesRepaymentsOfPrincipal"

FIELDS: dict[str, list[str]] = {
    "revenue": ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues"],
    "operating_income": ["OperatingIncomeLoss"],
    "da": ["DepreciationDepletionAndAmortization", "DepreciationAndAmortization",
           "DepreciationAmortizationAndAccretionNet"],
    "interest_expense": ["InterestExpenseDebt", "InterestExpense", "InterestExpenseNonoperating"],
    "interest_paid": ["InterestPaidNet", "InterestPaid"],
    "cfo": ["NetCashProvidedByUsedInOperatingActivities"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment"],
}


# --- fiscal calendar -------------------------------------------------------------------------

def _fye_date(fiscal_year_end: str, year: int) -> date:
    month, day = int(fiscal_year_end[:2]), int(fiscal_year_end[2:])
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def fiscal_year_of(d: date, fiscal_year_end: str) -> int:
    """Fiscal years are named by the calendar year they end in."""
    return d.year if d <= _fye_date(fiscal_year_end, d.year) + timedelta(days=TOLERANCE_DAYS) else d.year + 1


def fiscal_year_start(fy: int, fiscal_year_end: str) -> date:
    return _fye_date(fiscal_year_end, fy - 1) + timedelta(days=1)


def fiscal_year_end_date(fy: int, fiscal_year_end: str) -> date:
    return _fye_date(fiscal_year_end, fy)


def _months(a: date, b: date) -> int:
    return round((b - a).days / 30.44)


def _month_end_shift(d: date, months: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    return date(y, m + 1, calendar.monthrange(y, m + 1)[1])


def bucket_label(fiscal_year: int | None, months: int | None, calendar_fy: bool) -> str:
    if fiscal_year is None:
        return "Thereafter"
    yy = f"{fiscal_year % 100:02d}"
    if months is None or months >= 12:
        return str(fiscal_year) if calendar_fy else f"FY{yy}"
    part = {3: "Q4", 6: "2H", 9: "Q2–Q4"}.get(months, f"{months}M")
    if calendar_fy:
        return f"{part}{yy}" if part == "2H" else f"{part} {yy}"
    return f"FY{yy} {part}"


def quarter_label(end: date, fiscal_year_end: str) -> str:
    fy = fiscal_year_of(end, fiscal_year_end)
    q = max(1, min(4, _months(fiscal_year_start(fy, fiscal_year_end) - timedelta(days=1), end) // 3))
    yy = f"{fy % 100:02d}"
    return f"Q{q} {yy}" if fiscal_year_end == "1231" else f"Q{q} FY{yy}"


# --- fact lookup -----------------------------------------------------------------------------

def _usd(facts: dict, tag: str) -> list[dict]:
    return facts.get("facts", {}).get("us-gaap", {}).get(tag, {}).get("units", {}).get("USD", [])


def _instant(facts: dict, tag: str, period_end: date) -> Fact | None:
    rows = [r for r in _usd(facts, tag) if "start" not in r and r["end"] == period_end.isoformat()]
    if not rows:
        return None
    r = max(rows, key=lambda r: r.get("filed", ""))
    return Fact(value=r["val"] / 1e6, tag=tag, accession=r.get("accn"), period=r["end"])


def _first_instant(facts: dict, tags: list[str], period_end: date) -> Fact:
    for tag in tags:
        if (f := _instant(facts, tag, period_end)) is not None:
            return f
    return Fact(value=None, note=f"none of {', '.join(tags)} reported at {period_end}")


def _durations(facts: dict, tag: str) -> dict[tuple[date, date], float]:
    best: dict[tuple[date, date], dict] = {}
    for r in _usd(facts, tag):
        if "start" not in r:
            continue
        key = (date.fromisoformat(r["start"]), date.fromisoformat(r["end"]))
        if key not in best or r.get("filed", "") > best[key].get("filed", ""):
            best[key] = r
    return {k: r["val"] / 1e6 for k, r in best.items()}


def _find(durs: dict[tuple[date, date], float], start: date | None, end: date, days: range | None = None):
    tol = timedelta(days=TOLERANCE_DAYS)
    for (s, e), v in durs.items():
        if abs(e - end) > tol:
            continue
        if start is not None and abs(s - start) > tol:
            continue
        if days is not None and (e - s).days not in days:
            continue
        return v
    return None


def _quarter_value(durs, end: date, fiscal_year_end: str) -> float | None:
    direct = _find(durs, None, end, range(80, 101))
    if direct is not None:
        return direct
    fy_start = fiscal_year_start(fiscal_year_of(end, fiscal_year_end), fiscal_year_end)
    ytd = _find(durs, fy_start, end)
    prev_end = _month_end_shift(end, -3)
    prev = _find(durs, fy_start, prev_end) if prev_end >= fy_start else None
    if ytd is None or prev is None:
        return None
    return ytd - prev


# --- public ----------------------------------------------------------------------------------

def build_financials(companyfacts: dict, instance_xml: str, period_end: date, fiscal_year_end: str,
                     accession: str) -> Financials:
    total_debt = _total_debt(companyfacts, period_end)
    quarters = _quarters(companyfacts, period_end, fiscal_year_end)
    return Financials(
        period_end=period_end,
        fiscal_year_end=fiscal_year_end,
        total_debt=total_debt,
        cash=_first_instant(companyfacts, ["CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
                                           "CashAndCashEquivalentsAtCarryingValue"], period_end),
        op_lease=_first_instant(companyfacts, ["OperatingLeaseLiability"], period_end),
        fin_lease=_first_instant(companyfacts, ["FinanceLeaseLiability"], period_end),
        rpo=_first_instant(companyfacts, ["RevenueRemainingPerformanceObligation"], period_end),
        shares_outstanding=_shares(instance_xml, accession),
        maturities=_maturities(companyfacts, period_end, fiscal_year_end, total_debt.value),
        quarters=quarters,
        ttm=_ttm(quarters, period_end),
    )


def _total_debt(facts: dict, period_end: date) -> Fact:
    if (f := _instant(facts, "DebtInstrumentCarryingAmount", period_end)) is not None:
        return f
    stb = _instant(facts, "ShortTermBorrowings", period_end)
    # LongTermDebt already includes its current portion; adding LongTermDebtCurrent would double count.
    lt = _instant(facts, "LongTermDebt", period_end)
    parts: list[Fact] = [lt] if lt else [p for p in (_instant(facts, "LongTermDebtNoncurrent", period_end),
                                                     _instant(facts, "LongTermDebtCurrent", period_end)) if p]
    if not parts:
        return Fact(value=None, note=f"no total debt reported at {period_end}")
    parts += [stb] if stb else []
    return Fact(value=sum(p.value for p in parts), tag="+".join(p.tag for p in parts),
                accession=parts[0].accession, period=period_end.isoformat())


def _shares(instance_xml: str, accession: str) -> Fact:
    values = re.findall(r"<dei:EntityCommonStockSharesOutstanding\b[^>]*>\s*([\d.]+)\s*<", instance_xml)
    if not values:
        return Fact(value=None, note="no dei:EntityCommonStockSharesOutstanding in the filing")
    return Fact(value=float(sum(float(v) for v in values)), tag="dei:EntityCommonStockSharesOutstanding",
                accession=accession, note="cover page, all share classes")


def _maturities(facts: dict, period_end: date, fiscal_year_end: str, total: float | None) -> list[MaturityBucket]:
    fy = fiscal_year_of(period_end, fiscal_year_end)
    cal = fiscal_year_end == "1231"
    months_left = _months(period_end, fiscal_year_end_date(fy, fiscal_year_end))
    rem = _instant(facts, MATURITY + "RemainderOfFiscalYear", period_end)
    years = ["InNextTwelveMonths", "InYearTwo", "InYearThree", "InYearFour", "InYearFive"]
    buckets: list[MaturityBucket] = []
    if rem is not None and 0 < months_left < 12:
        buckets.append(MaturityBucket(label=bucket_label(fy, months_left, cal), fiscal_year=fy, months=months_left,
                                      principal=rem.value, tag=rem.tag))
    for i, suffix in enumerate(years, start=1):
        f = _instant(facts, MATURITY + suffix, period_end)
        if f is not None:
            buckets.append(MaturityBucket(label=bucket_label(fy + i, 12, cal), fiscal_year=fy + i, months=12,
                                          principal=f.value, tag=f.tag))
    if not buckets:
        return []
    after = _instant(facts, MATURITY + "AfterYearFive", period_end)
    if after is not None:
        buckets.append(MaturityBucket(label="Thereafter", fiscal_year=None, months=0, principal=after.value,
                                      tag=after.tag))
    elif total is not None:
        rest = total - sum(b.principal for b in buckets)
        buckets.append(MaturityBucket(label="Thereafter", fiscal_year=None, months=0, principal=max(rest, 0.0),
                                      tag=None))
    return buckets


def _quarters(facts: dict, period_end: date, fiscal_year_end: str) -> list[Quarter]:
    durs = {field: [_durations(facts, t) for t in tags] for field, tags in FIELDS.items()}
    out: list[Quarter] = []
    for i in range(7, -1, -1):
        end = _month_end_shift(period_end, -3 * i) if i else period_end
        values = {}
        for field, per_tag in durs.items():
            values[field] = next((v for d in per_tag if (v := _quarter_value(d, end, fiscal_year_end)) is not None),
                                 None)
        if values["revenue"] is None and values["operating_income"] is None:
            continue
        start = _month_end_shift(end, -3) + timedelta(days=1)
        out.append(Quarter(label=quarter_label(end, fiscal_year_end), start=start, end=end, **values))
    return out


def _ttm(quarters: list[Quarter], period_end: date) -> Quarter:
    last = quarters[-4:] if len(quarters) >= 4 else []
    sums = {}
    for field in FIELDS:
        vals = [getattr(q, field) for q in last]
        sums[field] = sum(vals) if last and None not in vals else None
    start = last[0].start if last else period_end
    return Quarter(label="TTM", start=start, end=period_end, **sums)
