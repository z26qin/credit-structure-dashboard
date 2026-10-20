"""Check the LLM's instrument list against the filing before anything is drawn.

Quotes must appear in the source text; instruments must add up to the reported debt total. A gap is kept
as an "Unallocated" instrument so charts still sum to the reported total; it is never filled in quietly.
"""
from __future__ import annotations

import calendar
import re
import unicodedata
from datetime import date

from .facts import fiscal_year_of
from .models import Extraction, Financials, Instrument, Reconciliation, ReconStatus

PASS_PCT, WARN_PCT = 0.01, 0.05
SCALE_TOLERANCE = 0.10
MONEY_FIELDS = ("principal", "post_period_change", "commitment")
QUOTE_CHARS = str.maketrans({"“": '"', "”": '"', "„": '"', "‘": "'", "’": "'", "–": "-", "—": "-", "‐": "-",
                             "−": "-", "\xa0": " ", "\u200b": "", "\ufeff": ""})


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(QUOTE_CHARS)
    return re.sub(r"\s+", " ", text).strip()


def unallocated(amount: float) -> Instrument:
    return Instrument(name="Unallocated", kind="other", seniority_class="other", principal=amount,
                      rate_type="unknown", amortization="unknown", source="reconcile", quotes=[])


def reconcile(extraction: Extraction | None, financials: Financials,
              sources: dict[str, str]) -> tuple[Reconciliation, list[Instrument]]:
    reported = financials.total_debt.value
    if extraction is None:
        rec = Reconciliation(status=ReconStatus.skipped, reported_total=reported,
                             notes=["no instrument detail: totals come from SEC data only"])
        return rec, [unallocated(reported)] if reported else []

    notes: list[str] = []
    instruments = [i.model_copy(deep=True) for i in extraction.instruments]
    scale = _scale(sum(i.principal or 0 for i in instruments), reported)
    if scale != 1.0:
        for i in instruments:
            for f in MONEY_FIELDS:
                if getattr(i, f) is not None:
                    setattr(i, f, getattr(i, f) * scale)
        notes.append(f"rescaled extraction by {scale:g} to $ millions")

    # A quote verifies if it appears in any filing text the model was given: an instrument's balance can come
    # from the 10-Q while its coupon and maturity are quoted from the 10-K.
    normalized = [normalize(v) for v in sources.values()]
    unverified = []
    for i in instruments:
        i.verified = bool(i.quotes) and all(any(normalize(q) in t for t in normalized) for q in i.quotes)
        if not i.verified:
            unverified.append(i.name)

    for i in instruments:
        d = maturity_date(i.maturity)
        if d is not None and d < financials.period_end and (i.principal or 0) > 0:
            notes.append(f"{i.name} matured {i.maturity}, before the period end {financials.period_end}: its balance "
                         "is likely from an older filing, so it is left out of the stack and the schedule")
    extracted = sum(i.principal or 0 for i in instruments)
    maturity_diff = _maturity_diff(instruments, financials)
    if reported is None:
        notes.append("no reported debt total to check against")
        return Reconciliation(status=ReconStatus.warn, extracted_total=extracted, scale_applied=scale,
                              unverified=unverified, maturity_diff=maturity_diff, notes=notes), instruments

    gap = reported - extracted
    gap_pct = gap / reported if reported else 0.0
    status = (ReconStatus.pass_ if abs(gap_pct) <= PASS_PCT else
              ReconStatus.warn if abs(gap_pct) <= WARN_PCT else ReconStatus.fail)
    if gap > 1e-6:
        instruments.append(unallocated(gap))
    elif gap < -1e-6:
        notes.append(f"extracted exceeds reported by {-gap:,.1f}")
    return Reconciliation(status=status, reported_total=reported, extracted_total=extracted, gap=gap,
                          gap_pct=gap_pct, scale_applied=scale, unverified=unverified,
                          maturity_diff=maturity_diff, notes=notes), instruments


def _scale(extracted: float, reported: float | None) -> float:
    if not reported or not extracted:
        return 1.0
    ratio = extracted / reported
    for factor in (1000.0, 0.001):
        if abs(ratio / factor - 1) <= SCALE_TOLERANCE:
            return 1 / factor
    return 1.0


def maturity_date(maturity: str | None) -> date | None:
    if not maturity or not re.fullmatch(r"\d{4}-\d{2}", maturity):
        return None
    y, m = int(maturity[:4]), int(maturity[5:])
    if not 1 <= m <= 12:
        return None
    return date(y, m, calendar.monthrange(y, m)[1])


def _maturity_diff(instruments: list[Instrument], financials: Financials) -> list[dict]:
    dated = [b for b in financials.maturities if b.fiscal_year is not None]
    last_fy = max((b.fiscal_year for b in dated), default=None)
    bullets: dict[str, float] = {b.label: 0.0 for b in financials.maturities}
    for i in instruments:
        d = maturity_date(i.maturity)
        if i.amortization != "bullet" or d is None or not i.principal:
            continue
        fy = fiscal_year_of(d, financials.fiscal_year_end)
        label = next((b.label for b in dated if b.fiscal_year == fy), None)
        if label is None and last_fy is not None and fy > last_fy:
            label = "Thereafter"
        if label in bullets:
            bullets[label] += i.principal
    return [{"label": b.label, "reported": b.principal, "bullets": bullets[b.label]} for b in financials.maturities]
