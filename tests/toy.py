"""A small made-up company the model, render and pipeline tests share.

Period end 2026-06-30, calendar fiscal year, reported principal due 2H26 100 / 2027 400 / 2028 200 /
Thereafter 300 ($M), SOFR 4%, market cap 1,000. Three instruments:
A 300 bullet, fixed 5%, Jun 2027, senior unsecured
B 400 amortizing, SOFR + 2%, Dec 2028, asset-level secured
C 300 bullet, fixed 9%, Mar 2031, convertible
"""
from datetime import date

from creditdash.config import ModelSettings
from creditdash.models import Fact, Financials, Instrument, Market, MaturityBucket, Quarter

PERIOD_END = date(2026, 6, 30)
SETTINGS = ModelSettings()


def quarter(label: str, end: date, op_income: float, da: float = 0.0, capex: float | None = 50.0,
            cfo: float | None = 60.0) -> Quarter:
    return Quarter(label=label, start=date(end.year, end.month - 2, 1), end=end, revenue=300.0,
                   operating_income=op_income, da=da, interest_expense=20.0, interest_paid=18.0, cfo=cfo,
                   capex=capex)


def financials(ttm_ebitda: float = 500.0, last_quarter_ebitda: float = 150.0) -> Financials:
    q = quarter("Q2 26", PERIOD_END, last_quarter_ebitda)
    ttm = quarter("TTM", PERIOD_END, ttm_ebitda, capex=200.0, cfo=240.0)
    buckets = [MaturityBucket(label="2H26", fiscal_year=2026, months=6, principal=100, tag=None),
               MaturityBucket(label="2027", fiscal_year=2027, months=12, principal=400, tag=None),
               MaturityBucket(label="2028", fiscal_year=2028, months=12, principal=200, tag=None),
               MaturityBucket(label="Thereafter", fiscal_year=None, months=0, principal=300, tag=None)]
    return Financials(period_end=PERIOD_END, fiscal_year_end="1231", total_debt=Fact(value=1000.0),
                      cash=Fact(value=150.0), op_lease=Fact(value=80.0), fin_lease=Fact(value=None),
                      rpo=Fact(value=2000.0), shares_outstanding=Fact(value=100e6), maturities=buckets,
                      quarters=[q], ttm=ttm)


def market(market_cap: float | None = 1000.0, sofr: float = 4.0) -> Market:
    return Market(sofr_pct=Fact(value=sofr), price=Fact(value=10.0), market_cap=Fact(value=market_cap))


def inst(name: str, principal: float | None, *, maturity: str | None, seniority: str, amortization: str,
         rate_type: str = "fixed", fixed: float | None = None, spread: float | None = None,
         post: float | None = None) -> Instrument:
    return Instrument(name=name, kind="term_loan", seniority_class=seniority, principal=principal,
                      post_period_change=post, rate_type=rate_type, fixed_rate_pct=fixed, spread_pct=spread,
                      benchmark="SOFR" if spread is not None else None, maturity=maturity,
                      amortization=amortization, source="0000000000-26-000001", quotes=[name])


TOY = [
    inst("A", 300, maturity="2027-06", seniority="senior_unsecured", amortization="bullet", fixed=5.0),
    inst("B", 400, maturity="2028-12", seniority="asset_level_secured", amortization="amortizing",
         rate_type="floating", spread=2.0),
    inst("C", 300, maturity="2031-03", seniority="convertible", amortization="bullet", fixed=9.0),
]
FIN = financials()
MKT = market()
