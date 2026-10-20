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

PROMPT_VERSION = "extract_v1"
PROMPT_FILE = Path(__file__).parent / "prompts" / f"{PROMPT_VERSION}.md"


def build_inputs(resolved: Resolved, instance_xml: str, filing_html: Callable[[], str],
                 eight_ks: dict[str, str]) -> tuple[str | None, dict[str, str]]:
    """(user prompt, or None when no debt footnote is found; accession -> source text for quote checks)."""
    note = debt_note_from_instance(instance_xml) or debt_note_from_html(filing_html())
    if not note:
        return None, {}
    p = resolved.periodic
    sources = {p.accession: note}
    parts = [f"## Periodic filing {p.form} {p.accession}, period end {p.period_end}\n\n{note}"]
    for f in resolved.debt_8ks:
        text = eight_k_items(eight_ks.get(f.accession, ""))
        if text:
            sources[f.accession] = text
            parts.append(f"## 8-K {f.accession} filed {f.filed}\n\n{text}")
    return "\n\n".join(parts), sources


def extract(resolved: Resolved, instance_xml: str, filing_html: Callable[[], str], eight_ks: dict[str, str], *,
            ask: Callable = llm.ask, settings: Settings,
            use_cache: bool = True) -> tuple[Extraction | None, str | None, dict[str, str]]:
    prompt, sources = build_inputs(resolved, instance_xml, filing_html, eight_ks)
    if prompt is None:
        return None, "debt footnote not found", sources
    try:
        extraction, _ = ask(prompt, PROMPT_FILE.read_text(), Extraction, settings=settings, use_cache=use_cache)
    except LlmError as e:
        return None, f"LLM failed: {e}", sources
    return extraction, None, sources
