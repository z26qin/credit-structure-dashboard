"""Data passed between pipeline stages. Money is $ millions (float) everywhere."""
from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, computed_field


# --- filings ---------------------------------------------------------------------------------

class Filing(BaseModel):
    form: str
    accession: str
    filed: date
    period_end: date | None = None
    primary_doc: str
    items: list[str] = []


class Company(BaseModel):
    ticker: str
    cik: int
    name: str
    fiscal_year_end: str  # SEC "MMDD", e.g. "1231"


class Resolved(BaseModel):
    company: Company
    periodic: Filing
    debt_8ks: list[Filing]


# --- reported numbers ------------------------------------------------------------------------

class Fact(BaseModel):
    value: float | None
    tag: str | None = None
    accession: str | None = None
    period: str | None = None
    note: str = ""


class MaturityBucket(BaseModel):
    label: str
    fiscal_year: int | None  # None for "Thereafter"
    months: int  # 0 for "Thereafter"
    principal: float
    tag: str | None


class Quarter(BaseModel):
    label: str
    start: date
    end: date
    revenue: float | None
    operating_income: float | None
    da: float | None
    interest_expense: float | None
    interest_paid: float | None
    cfo: float | None
    capex: float | None

    @computed_field
    @property
    def ebitda(self) -> float | None:
        if self.operating_income is None or self.da is None:
            return None
        return self.operating_income + self.da


class Financials(BaseModel):
    period_end: date
    fiscal_year_end: str
    total_debt: Fact
    cash: Fact
    op_lease: Fact
    fin_lease: Fact
    rpo: Fact
    shares_outstanding: Fact
    maturities: list[MaturityBucket]
    quarters: list[Quarter]
    ttm: Quarter


# --- instruments -----------------------------------------------------------------------------

class Kind(StrEnum):
    term_loan = "term_loan"
    delayed_draw_term_loan = "delayed_draw_term_loan"
    revolver = "revolver"
    senior_notes = "senior_notes"
    convertible_notes = "convertible_notes"
    vendor_financing = "vendor_financing"
    finance_lease = "finance_lease"
    other = "other"


class SeniorityClass(StrEnum):
    """Declaration order is seniority order, most senior first."""
    asset_level_secured = "asset_level_secured"
    parent_secured = "parent_secured"
    senior_unsecured = "senior_unsecured"
    convertible = "convertible"
    other = "other"


SENIORITY_LABELS: dict[str, str] = {
    "asset_level_secured": "Asset-level secured",
    "parent_secured": "Parent secured",
    "senior_unsecured": "Senior unsecured",
    "convertible": "Convertible",
    "other": "Other",
    "equity": "Equity",
}


class RateType(StrEnum):
    fixed = "fixed"
    floating = "floating"
    unknown = "unknown"


class Amortization(StrEnum):
    bullet = "bullet"
    amortizing = "amortizing"
    unknown = "unknown"


class Instrument(BaseModel):
    name: str
    kind: Kind
    seniority_class: SeniorityClass
    borrower: str | None = None
    recourse_to_parent: bool | None = None
    security: str | None = None
    principal: float | None = None
    post_period_change: float | None = None
    commitment: float | None = None
    rate_type: RateType = RateType.unknown
    fixed_rate_pct: float | None = None
    spread_pct: float | None = None
    benchmark: str | None = None
    effective_rate_pct: float | None = None
    maturity: str | None = Field(default=None, description="YYYY-MM")
    amortization: Amortization = Amortization.unknown
    currency: str = "USD"
    source: str = Field(description="accession number the fields came from")
    quotes: list[str] = Field(default=[], description="1-3 verbatim snippets, each at most 300 characters")
    verified: bool = True  # set by reconcile; not part of the LLM schema


class Extraction(BaseModel):
    period_end: date
    unit_note: str
    instruments: list[Instrument]

    @classmethod
    def llm_schema(cls) -> dict:
        schema = cls.model_json_schema()
        inst = schema["$defs"]["Instrument"]
        inst["properties"].pop("verified", None)
        if "verified" in inst.get("required", []):
            inst["required"].remove("verified")
        return schema


# --- checks and market -----------------------------------------------------------------------

class ReconStatus(StrEnum):
    pass_ = "pass"
    warn = "warn"
    fail = "fail"
    skipped = "skipped"


class Reconciliation(BaseModel):
    status: ReconStatus
    reported_total: float | None = None
    extracted_total: float | None = None
    gap: float | None = None
    gap_pct: float | None = None
    scale_applied: float = 1.0
    unverified: list[str] = []
    maturity_diff: list[dict] = []
    fit_error: float | None = None
    notes: list[str] = []


class ConsensusYear(BaseModel):
    fiscal_year: int
    revenue: float | None = None
    ebitda: float | None = None
    capex: float | None = None
    cfo: float | None = None


class Market(BaseModel):
    sofr_pct: Fact
    price: Fact
    market_cap: Fact
    consensus: list[ConsensusYear] = []
    ratings: dict[str, str] = {}
    bond_yields: dict[str, float] = {}
    ebitda_label: str = "EBITDA (operating income + D&A)"


# --- model output ----------------------------------------------------------------------------

class Period(BaseModel):
    label: str
    fiscal_year: int
    months: int
    start: date
    end: date


class ServiceRow(BaseModel):
    instrument: str
    seniority_class: SeniorityClass
    period: str
    principal: float
    interest: float


class ScenarioRow(BaseModel):
    scenario: str
    period: str
    ebitda: float


class CoverageRow(BaseModel):
    scenario: str
    period: str
    dscr: float | None


class RaiseRow(BaseModel):
    period: str
    capex: float
    principal: float
    cfo: float
    to_raise: float


class StackClass(BaseModel):
    seniority_class: SeniorityClass | Literal["equity"]
    label: str
    amount: float
    share: float
    attachment: float


class WaterfallRow(BaseModel):
    period: str
    scenario: str
    seniority_class: SeniorityClass
    due: float
    paid: float
    shortfall: float


class ModelOutput(BaseModel):
    periods: list[Period]
    debt_service: list[ServiceRow]
    scenarios: list[ScenarioRow]
    coverage: list[CoverageRow]
    raise_rows: list[RaiseRow]
    stack: list[StackClass]
    waterfall: list[WaterfallRow]
    excluded: list[str]
    fit_error: float | None


class Dashboard(BaseModel):
    company: Company
    filing: Filing
    built_at: datetime
    financials: Financials
    extraction: Extraction | None
    extraction_skipped: str | None
    instruments: list[Instrument]
    reconciliation: Reconciliation
    market: Market
    model: ModelOutput
    settings: dict
