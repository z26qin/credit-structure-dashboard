"""Market-implied default risk (CDS or the worst bond) checked against cash flow.

Implied hazard λ = spread / (1 − recovery); cumulative PD over t years = 1 − exp(−λ t). The spread is the
5-year CDS from overrides, else the highest bond yield-to-worst less SOFR. The model then judges, from the
dashboard's own coverage figures only, whether that price looks like an overreaction; every metric it cites
must be one it was given.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel

from . import llm
from .config import Settings
from .errors import LlmError
from .models import Financials, Market, ModelOutput

PROMPT_VERSION = "risk_v2"
PROMPT_FILE = Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md"


class Implied(BaseModel):
    basis: str  # "5-year CDS" or "worst bond: <name>"
    spread_bps: float
    recovery: float
    hazard: float
    pd_1y: float
    pd_5y: float


class Assessment(BaseModel):
    verdict: Literal["overreaction", "fair", "underpricing", "unclear"]
    summary: str
    cited: list[str]


class RiskView(BaseModel):
    implied: Implied | None
    metrics: dict[str, float | None]
    assessment: Assessment | None = None
    note: str = ""


def implied(market: Market, recovery: float) -> Implied | None:
    if market.cds_bps.value is not None:
        basis, spread = "5-year CDS", market.cds_bps.value
    elif market.bond_yields and market.sofr_pct.value is not None:
        name, y = max(market.bond_yields.items(), key=lambda kv: kv[1])
        basis, spread = f"worst bond: {name} ({y:.2f}% yield)", (y - market.sofr_pct.value) * 100
    else:
        return None
    hazard = max(spread, 0) / 10_000 / (1 - recovery)
    return Implied(basis=basis, spread_bps=spread, recovery=recovery, hazard=hazard,
                   pd_1y=1 - math.exp(-hazard), pd_5y=1 - math.exp(-5 * hazard))


def metrics(f: Financials, m: ModelOutput) -> dict[str, float | None]:
    """The coverage figures the model may cite, all from the model's headline (one place computes them)."""
    t, h = f.ttm, m.headline
    tests = {x.key: x.value for x in h.tests} if h else {}
    div = lambda a, b: None if a is None or not b else a / b
    fcf = None if t.cfo is None or t.capex is None else t.cfo - t.capex
    net = None if f.total_debt.value is None or f.cash.value is None else f.total_debt.value - f.cash.value
    i12, p12 = (h.next12m_interest, h.next12m_principal) if h else (None, None)
    return {
        "ttm_ebitda": t.ebitda, "ttm_cfo": t.cfo, "ttm_capex": t.capex, "ttm_fcf": fcf,
        "next12m_interest": i12, "next12m_principal": p12,
        "next12m_refinancing_interest": h.next12m_refinancing_interest if h else None,
        "ebitda_to_next12m_interest": tests.get("ebitda_to_next12m_interest"),
        "cfo_plus_interest_to_interest": tests.get("cfo_plus_interest_to_interest"),
        "fcf_plus_interest_to_interest": tests.get("fcf_plus_interest_to_interest"),
        "cash": f.cash.value, "undrawn_facilities": h.undrawn if h else None,
        "liquidity_to_next12m_debt_service": tests.get("liquidity_to_next12m_debt_service"),
        "cash_to_next12m_debt_service": div(f.cash.value, (i12 or 0) + (p12 or 0)),
        "net_debt_to_ebitda": div(net, t.ebitda),
    }


def assess(imp: Implied, mets: dict, *, ask: Callable = llm.ask, settings: Settings,
           use_cache: bool = True) -> tuple[Assessment | None, str]:
    facts = {"implied": imp.model_dump(), "metrics": {k: (None if v is None else round(v, 4)) for k, v in mets.items()}}
    try:
        a, _ = ask(json.dumps(facts, indent=1), PROMPT_FILE.read_text(), Assessment, settings=settings, use_cache=use_cache)
    except LlmError as e:
        return None, f"LLM failed: {e}"
    bad = [c for c in a.cited if c not in mets and c not in Implied.model_fields]
    if bad or not a.cited:
        return None, "the model cited figures it was not given: " + ", ".join(bad) if bad else "the model cited no figures"
    return a, ""


def risk_view(f: Financials, m: ModelOutput, market: Market, recovery: float, *, use_llm: bool,
              ask: Callable = llm.ask, settings: Settings, use_cache: bool = True) -> RiskView:
    imp, mets = implied(market, recovery), metrics(f, m)
    if imp is None:
        return RiskView(implied=None, metrics=mets,
                        note="No CDS or bond yield: add cds_5y_bps or bond_yields to overrides/<TICKER>.yaml.")
    if not use_llm:
        return RiskView(implied=imp, metrics=mets, note="Interpretation skipped (--no-llm).")
    a, note = assess(imp, mets, ask=ask, settings=settings, use_cache=use_cache)
    return RiskView(implied=imp, metrics=mets, assessment=a, note=note)
