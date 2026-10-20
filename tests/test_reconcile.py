from datetime import date

import pytest

from creditdash.models import Extraction, Fact, Financials, Instrument, MaturityBucket, Quarter, ReconStatus
from creditdash.reconcile import normalize, reconcile

SOURCE = "| Term Loan | Mar 2029 | 7% | $400 |\nThe “Notes” bear interest at 9.25% and mature in 2030."
SOURCES = {"A": SOURCE}


def fin(total: float | None = 1000.0) -> Financials:
    q = Quarter(label="TTM", start=date(2025, 7, 1), end=date(2026, 6, 30), revenue=None, operating_income=None,
                da=None, interest_expense=None, interest_paid=None, cfo=None, capex=None)
    buckets = [MaturityBucket(label="2H26", fiscal_year=2026, months=6, principal=0, tag=None),
               MaturityBucket(label="2029", fiscal_year=2029, months=12, principal=400, tag=None),
               MaturityBucket(label="Thereafter", fiscal_year=None, months=0, principal=600, tag=None)]
    return Financials(period_end=date(2026, 6, 30), fiscal_year_end="1231", total_debt=Fact(value=total),
                      cash=Fact(value=None), op_lease=Fact(value=None), fin_lease=Fact(value=None),
                      rpo=Fact(value=None), shares_outstanding=Fact(value=None), maturities=buckets,
                      quarters=[], ttm=q)


def inst(name, principal, quote="| Term Loan | Mar 2029 | 7% | $400 |", maturity="2029-03", **kw) -> Instrument:
    return Instrument(name=name, kind="term_loan", seniority_class="parent_secured", principal=principal,
                      maturity=maturity, amortization="bullet", source="A", quotes=[quote], **kw)


def ext(*instruments) -> Extraction:
    return Extraction(period_end=date(2026, 6, 30), unit_note="$ millions", instruments=list(instruments))


def test_normalize():
    assert normalize("“DDTL 4.0” — due\n 2032") == '"DDTL 4.0" - due 2032'


def test_pass_within_1pct():
    rec, insts = reconcile(ext(inst("Term Loan", 400), inst("Notes", 595)), fin(), SOURCES)
    assert rec.status == ReconStatus.pass_
    assert (rec.reported_total, rec.extracted_total, rec.gap) == (1000, 995, 5)
    assert insts[-1].name == "Unallocated" and insts[-1].principal == 5 and insts[-1].maturity is None


def test_warn_and_fail():
    assert reconcile(ext(inst("Term Loan", 970)), fin(), SOURCES)[0].status == ReconStatus.warn
    assert reconcile(ext(inst("Term Loan", 920)), fin(), SOURCES)[0].status == ReconStatus.fail


def test_unverified_quote():
    rec, insts = reconcile(ext(inst("Term Loan", 400), inst("Ghost", 600, quote="not in the filing")), fin(), SOURCES)
    assert rec.unverified == ["Ghost"]
    assert [i.verified for i in insts if i.name != "Unallocated"] == [True, False]
    assert rec.extracted_total == 1000


def test_quote_matches_after_normalizing():
    rec, _ = reconcile(ext(inst("Notes", 1000, quote='The "Notes" bear  interest at 9.25%')), fin(), SOURCES)
    assert rec.unverified == []


def test_quote_from_another_sent_filing_verifies():  # balance from the 10-Q, detail quoted from the 10-K
    sources = {"A": SOURCE, "K": "| Term Loan | September 2029 | SOFR + 4.25% | $400 |"}
    quote = "| Term Loan | September 2029 | SOFR + 4.25% | $400 |"
    rec, _ = reconcile(ext(inst("Term Loan", 1000, quote=quote)), fin(), sources)
    assert rec.unverified == []


def test_quote_in_no_sent_filing_is_unverified():
    rec, _ = reconcile(ext(inst("X", 1000, quote="made up").model_copy(update={"source": "B"})), fin(), SOURCES)
    assert rec.unverified == ["X"]


def test_scale_thousands():
    rec, insts = reconcile(ext(inst("Term Loan", 400_000), inst("Notes", 600_000)), fin(), SOURCES)
    assert rec.scale_applied == 0.001 and rec.status == ReconStatus.pass_
    assert any("rescaled" in n for n in rec.notes)
    assert [i.principal for i in insts] == [400, 600]


def test_extracted_exceeds():
    rec, insts = reconcile(ext(inst("Term Loan", 1003)), fin(), SOURCES)
    assert rec.status == ReconStatus.pass_ and all(i.name != "Unallocated" for i in insts)
    assert any("exceeds" in n for n in rec.notes)


def test_skipped():
    rec, insts = reconcile(None, fin(), {})
    assert rec.status == ReconStatus.skipped
    assert [(i.name, i.principal) for i in insts] == [("Unallocated", 1000)]


def test_no_reported_total():
    rec, insts = reconcile(ext(inst("Term Loan", 400)), fin(total=None), SOURCES)
    assert rec.status == ReconStatus.warn and rec.gap is None and len(insts) == 1


def test_maturity_diff():
    rec, _ = reconcile(ext(inst("Term Loan", 400), inst("Notes", 600, maturity="2031-06")), fin(), SOURCES)
    by = {d["label"]: d for d in rec.maturity_diff}
    assert (by["2029"]["reported"], by["2029"]["bullets"]) == (400, 400)
    assert by["Thereafter"]["bullets"] == 600
