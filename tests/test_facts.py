import json
from datetime import date

import pytest

from creditdash.facts import bucket_label, build_financials
from tests.conftest import FIXTURES


@pytest.fixture(scope="module")
def fin():
    facts = json.loads((FIXTURES / "companyfacts.json").read_text())
    instance = (FIXTURES / "instance.xml").read_text()
    return build_financials(facts, instance, date(2026, 6, 30), "1231", "0001769628-26-000366")


def test_crwv_balances(fin):
    assert fin.total_debt.value == 35551.0 and fin.total_debt.tag == "DebtInstrumentCarryingAmount"
    assert (fin.cash.value, fin.op_lease.value, fin.rpo.value) == (6904.0, 16319.0, 103700.0)
    assert fin.shares_outstanding.value == 551_536_602


def test_crwv_maturities_shift(fin):
    assert [(b.label, round(b.principal)) for b in fin.maturities] == [
        ("2H26", 4413), ("2027", 6184), ("2028", 4416), ("2029", 2421), ("2030", 3221), ("Thereafter", 14896)]
    assert [b.fiscal_year for b in fin.maturities] == [2026, 2027, 2028, 2029, 2030, None]
    assert fin.maturities[0].months == 6


def test_crwv_q2_quarter(fin):
    q = fin.quarters[-1]
    assert (q.label, q.revenue, q.operating_income, q.da, q.ebitda, q.interest_expense, q.cfo) == \
           ("Q2 26", 2575.0, -49.0, 1393.0, 1344.0, 558.0, 679.0)
    assert round(fin.quarters[-3].revenue, 3) == 1571.904  # Q4 25 = FY - 9M
    assert fin.quarters[-3].label == "Q4 25"


def test_crwv_ttm(fin):
    assert fin.ttm.label == "TTM"
    assert fin.ttm.revenue == pytest.approx(sum(q.revenue for q in fin.quarters[-4:]))
    assert len(fin.quarters) <= 8


def test_bucket_labels():
    assert bucket_label(2026, 9, True) == "Q2–Q4 26" and bucket_label(2027, 9, False) == "FY27 Q2–Q4"
    assert bucket_label(2026, 6, True) == "2H26" and bucket_label(2026, 3, True) == "Q4 26"
    assert bucket_label(2028, 12, False) == "FY28" and bucket_label(2027, 12, True) == "2027"
    assert bucket_label(None, None, True) == "Thereafter"


def usd(start, end, val, filed="2026-09-30"):
    d = {"end": end, "val": val, "accn": "x", "fy": 2027, "fp": "Q1", "form": "10-Q", "filed": filed}
    if start:
        d["start"] = start
    return d


SYNTH_0531 = {"facts": {"us-gaap": {
    "OperatingIncomeLoss": {"units": {"USD": [usd("2026-06-01", "2026-08-31", 50e6)]}},
    "DepreciationDepletionAndAmortization": {"units": {"USD": [usd("2026-06-01", "2026-08-31", 10e6)]}},
    "DebtInstrumentCarryingAmount": {"units": {"USD": [usd(None, "2026-08-31", 1000e6)]}},
    "LongTermDebtMaturitiesRepaymentsOfPrincipalRemainderOfFiscalYear":
        {"units": {"USD": [usd(None, "2026-08-31", 100e6)]}},
    "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths":
        {"units": {"USD": [usd(None, "2026-08-31", 200e6)]}},
}}}


def test_non_calendar_fiscal_year():
    fin = build_financials(SYNTH_0531, "<xbrl/>", date(2026, 8, 31), "0531", "x")
    assert [(b.label, b.principal) for b in fin.maturities] == [
        ("FY27 Q2–Q4", 100.0), ("FY28", 200.0), ("Thereafter", 700.0)]
    assert fin.quarters[-1].label == "Q1 FY27" and fin.quarters[-1].ebitda == 60.0
    assert fin.shares_outstanding.value is None


def test_long_term_debt_fallback_does_not_double_count_current():
    facts = {"facts": {"us-gaap": {
        "LongTermDebt": {"units": {"USD": [usd(None, "2026-06-30", 900e6)]}},
        "LongTermDebtCurrent": {"units": {"USD": [usd(None, "2026-06-30", 100e6)]}},
        "ShortTermBorrowings": {"units": {"USD": [usd(None, "2026-06-30", 50e6)]}},
    }}}
    fin = build_financials(facts, "<xbrl/>", date(2026, 6, 30), "1231", "x")
    assert fin.total_debt.value == 950.0
