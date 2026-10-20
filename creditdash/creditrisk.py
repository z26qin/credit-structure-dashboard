"""Market-implied default risk (CDS or the worst bond) checked against cash flow.

Implied hazard λ = spread / (1 − recovery); cumulative PD over t years = 1 − exp(−λ t). The spread is the
5-year CDS from overrides, else the highest bond yield-to-worst less SOFR. The model then judges, from the
dashboard's own coverage figures only, whether that price looks like an overreaction. It writes no numbers:
it names figures as {key} placeholders and code fills in the formatted values, so every number on the page is
one it was given.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Callable, Literal

from pydantic import BaseModel

from . import llm
from .config import Settings
from .errors import LlmError
from .model import money
from .models import Financials, Market, ModelOutput

PROMPT_VERSION = "risk_v3"
PROMPT_FILE = Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md"


class Implied(BaseModel):
    basis: str  # "5-year CDS" or "worst bond: <name>"
    spread_bps: float
    recovery: float
    hazard: float
    pd_1y: float
    pd_5y: float


PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_]+)\}")
SPOKEN_PERIOD = re.compile(r"\b(?:1|5|12)[- ](?:year|month)s?\b")  # "next 12 months" is not a figure
OWN_UNIT = re.compile(r"\{\w+_to_\w+\}\s*(?:times|x)\b|\b(?:negative|minus)\s+(?:of\s+)?\{", re.I)
Verdict = Literal["overreaction", "fair", "underpricing", "unclear"]


class Draft(BaseModel):
    """What the model writes: short sentences that name figures as {key} placeholders."""
    verdict: Verdict
    reasons: list[str]
    watch: str = ""


class Assessment(BaseModel):
    """The draft with every placeholder filled in by code."""
    verdict: Verdict
    reasons: list[str]
    watch: str = ""
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


def formatted(key: str, value) -> str:
    if isinstance(value, str):
        return value
    if key in ("pd_1y", "pd_5y", "hazard"):
        return f"{value * 100:.1f}%"
    if key == "recovery":
        return f"{value * 100:.0f}%"
    if key == "spread_bps":
        return f"{value:,.0f}bp"
    if "_to_" in key:
        return f"{value:.1f}x".replace("-", "−")
    return money(value)


def readable(draft: Draft, values: dict) -> tuple[Assessment, list[str]]:
    """Fill in the placeholders. Problems: a figure that is unknown or null, or a number the model wrote itself."""
    cited, problems = [], []

    def fill(text: str) -> str:
        for key in PLACEHOLDER.findall(text):
            if values.get(key) is None:
                problems.append(f"{{{key}}} is not a figure you were given")
            elif key not in cited:
                cited.append(key)
        if any(ch.isdigit() for ch in SPOKEN_PERIOD.sub("", PLACEHOLDER.sub("", text))):
            problems.append(f'"{text}" has a number you wrote yourself; use a {{key}} placeholder')
        if OWN_UNIT.search(text):
            problems.append(f'"{text}" adds "times" or a sign: the value already carries its unit and sign')
        return PLACEHOLDER.sub(lambda m: formatted(m.group(1), values[m.group(1)])
                               if values.get(m.group(1)) is not None else m.group(0), text)

    reasons = [fill(r) for r in draft.reasons if r.strip()]
    if not reasons:
        problems.append("no reasons given")
    return Assessment(verdict=draft.verdict, reasons=reasons, watch=fill(draft.watch), cited=cited), problems


def assess(imp: Implied, mets: dict, *, ask: Callable = llm.ask, settings: Settings,
           use_cache: bool = True) -> tuple[Assessment | None, str]:
    """One draft, sent back once with its problems if it has any. The model sees each figure as the page will
    show it, so its sentence reads right around the filled-in value."""
    values = {**imp.model_dump(), **mets}
    facts = {"figures": {k: formatted(k, v) for k, v in values.items() if v is not None},
             "not_available": [k for k, v in values.items() if v is None]}
    prompt = json.dumps(facts, indent=1, ensure_ascii=False)
    for _ in range(2):
        try:
            draft, _ = ask(prompt, PROMPT_FILE.read_text(), Draft, settings=settings, use_cache=use_cache)
        except LlmError as e:
            return None, f"LLM failed: {e}"
        a, problems = readable(draft, values)
        if not problems:
            return a, ""
        prompt = (json.dumps(facts, indent=1, ensure_ascii=False) + "\n\nYour last reply was sent back: " + "; ".join(problems) +
                  ". It was:\n" + draft.model_dump_json())
    return None, "the model's read was left out: " + "; ".join(problems)


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
