"""The one LLM step: the debt footnote (and debt 8-Ks) -> a typed instrument list.

Code finds the text; the model only reads it. Any failure returns a reason instead of an extraction, and
the dashboard is then built from SEC totals alone.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import llm
from .config import Settings
from .errors import LlmError
from .footnote import debt_note_from_html, debt_note_from_instance, eight_k_items
from .models import Extraction, Resolved

PROMPT_VERSION = "extract_v3"
PROMPT_FILE = Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md"


def build_inputs(resolved: Resolved, instance_xml: str, filing_html: Callable[[], str],
                 eight_ks: dict[str, str], annual_instance: str | None = None) -> tuple[str | None, dict[str, str]]:
    """(user prompt, or None when no debt footnote is found; accession -> source text for quote checks).

    Quarterly filings often list debt only in category totals, or not at all, leaving the instruments to
    the 10-K; the latest 10-K's debt note is then added for detail."""
    note = debt_note_from_instance(instance_xml) or debt_note_from_html(filing_html())
    a = resolved.annual
    annual_note = debt_note_from_instance(annual_instance) if a and annual_instance else None
    if not note and not annual_note:
        return None, {}
    p = resolved.periodic
    sources: dict[str, str] = {}
    head = f"## Periodic filing {p.form} {p.accession}, period end {p.period_end}"
    if note:
        sources[p.accession] = note
        parts = [f"{head}\n\n{note}"]
    else:
        parts = [f"{head}\n\nThis filing has no debt note; use the annual filing."]
    if annual_note:
        sources[a.accession] = annual_note
        parts.append(f"## Annual filing {a.form} {a.accession}, period end {a.period_end}\n\n{annual_note}")
    for f in resolved.debt_8ks:
        text = eight_k_items(eight_ks.get(f.accession, ""))
        if text:
            sources[f.accession] = text
            parts.append(f"## 8-K {f.accession} filed {f.filed}\n\n{text}")
    return "\n\n".join(parts), sources


def extract(resolved: Resolved, instance_xml: str, filing_html: Callable[[], str], eight_ks: dict[str, str], *,
            annual_instance: str | None = None, ask: Callable = llm.ask, settings: Settings,
            use_cache: bool = True) -> tuple[Extraction | None, str | None, dict[str, str]]:
    prompt, sources = build_inputs(resolved, instance_xml, filing_html, eight_ks, annual_instance)
    if prompt is None:
        return None, "debt footnote not found", sources
    try:
        extraction, _ = ask(prompt, PROMPT_FILE.read_text(), Extraction, settings=settings, use_cache=use_cache)
    except LlmError as e:
        return None, f"LLM failed: {e}", sources
    return extraction, None, sources
