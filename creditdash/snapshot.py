"""Dashboard -> a compact summary for a model to read: the top card, the debt list and the checks, without the
monthly schedules the page draws. Money is $ millions, rounded to 0.1; ratios to 0.01.

Each instrument carries its period-end principal and its post-period change apart, and their sum: the stack and
the model use the sum, the reconciliation the period-end figure."""
from __future__ import annotations

from .models import SENIORITY_LABELS, Dashboard, Instrument


def _m(v: float | None) -> float | None:
    return None if v is None else round(v, 1)


def _x(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


def _rate(i: Instrument) -> str | None:
    if i.fixed_rate_pct is not None:
        return f"{i.fixed_rate_pct:g}% fixed"
    if i.spread_pct is not None:
        return f"{i.benchmark or 'benchmark'} + {i.spread_pct:g}%"
    if i.effective_rate_pct is not None:
        return f"{i.effective_rate_pct:g}% effective"
    return None


def snapshot(d: Dashboard) -> dict:
    fin, rec, model, market = d.financials, d.reconciliation, d.model, d.market
    h = model.headline
    out: dict = {
        "units": "$ millions; ratios as multiples",
        "company": {"ticker": d.company.ticker, "name": d.company.name, "cik": d.company.cik},
        "filing": {"form": d.filing.form, "accession": d.filing.accession, "filed": d.filing.filed.isoformat(),
                   "period_end": fin.period_end.isoformat()},
        "built_at": d.built_at.isoformat(),
    }
    if h is not None:
        out["can_it_pay_interest"] = {
            "bottom_line": h.bottom_line,
            "funding": h.funding,
            "earnings": _m(h.earnings),
            "earnings_basis": h.earnings_basis,
            "tests": [{"label": t.label, "value": _x(t.value), "status": t.status, "detail": t.detail}
                      for t in h.tests],
            "next12m_interest": _m(h.next12m_interest),
            "next12m_principal": _m(h.next12m_principal),
            "next12m_refinancing_interest": _m(h.next12m_refinancing_interest),
            "refinancing_rate_pct": _x(h.refinancing_rate_pct),
            "liquidity": _m(h.liquidity),
            "undrawn_revolvers": _m(h.undrawn),
        }
    out["balance_sheet"] = {
        "total_debt_reported": _m(fin.total_debt.value), "cash": _m(fin.cash.value),
        "operating_leases": _m(fin.op_lease.value), "finance_leases": _m(fin.fin_lease.value),
        "backlog_rpo": _m(fin.rpo.value),
    }
    t = fin.ttm
    out["ttm"] = {"period": t.label, "revenue": _m(t.revenue), "ebitda": _m(t.ebitda), "stock_comp": _m(t.sbc),
                  "interest_expense": _m(t.interest_expense), "interest_paid": _m(t.interest_paid),
                  "operating_cash_flow": _m(t.cfo), "capex": _m(t.capex)}
    out["reconciliation"] = {
        "status": rec.status.value, "reported_total": _m(rec.reported_total), "extracted_total": _m(rec.extracted_total),
        "gap": _m(rec.gap), "gap_pct": _x(rec.gap_pct), "unverified": rec.unverified, "notes": rec.notes,
    }
    if d.extraction_skipped:
        out["reconciliation"]["extraction_skipped"] = d.extraction_skipped
    out["instruments"] = [
        {"name": i.name, "kind": i.kind.value, "seniority": SENIORITY_LABELS[i.seniority_class.value],
         "principal_at_period_end": _m(i.principal), "post_period_change": _m(i.post_period_change),
         "balance_pro_forma": _m(max((i.principal or 0) + (i.post_period_change or 0), 0)),
         "commitment": _m(i.commitment), "rate": _rate(i), "maturity": i.maturity,
         "amortization": i.amortization.value, "secured_by": i.security, "verified": i.verified, "source": i.source}
        for i in d.instruments
    ]
    out["maturities"] = {"as_of": fin.maturities_as_of.isoformat() if fin.maturities_as_of else None,
                         "buckets": [{"label": b.label, "principal": _m(b.principal)} for b in fin.maturities]}
    if not fin.maturities:
        out["maturities"]["note"] = ("The filings tag no maturity schedule (usual for 20-F filers), so each "
                                     "instrument is repaid at its own maturity per its amortization.")
    out["capital_stack"] = {
        "basis": "pro forma: period-end principal plus post-period changes (balance_pro_forma), equity at market cap",
        "classes": [{"class": s.label, "amount": _m(s.amount), "share": _x(s.share), "attachment": _m(s.attachment)}
                    for s in model.stack]}
    out["market"] = {
        "price": market.price.value, "market_cap": _m(market.market_cap.value), "sofr_pct": market.sofr_pct.value,
        "cds_5y_bps": market.cds_bps.value, "bond_yields_pct": market.bond_yields, "ratings": market.ratings,
        "consensus_years": [c.fiscal_year for c in market.consensus],
    }
    if d.risk is not None:
        r = d.risk
        risk: dict = {"note": r.note} if r.note else {}
        if r.implied is not None:
            risk["implied_default"] = {"basis": r.implied.basis, "spread_bps": round(r.implied.spread_bps),
                                       "recovery": r.implied.recovery, "pd_1y": round(r.implied.pd_1y, 3),
                                       "pd_5y": round(r.implied.pd_5y, 3)}
        if r.assessment is not None:
            risk["model_read"] = {"verdict": r.assessment.verdict, "reasons": r.assessment.reasons,
                                  "watch": r.assessment.watch}
        out["market_vs_cash_flow"] = risk
    if d.basis:
        out["basis"] = d.basis
    if model.excluded:
        out["excluded_from_model"] = model.excluded
    return out
