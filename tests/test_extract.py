from datetime import date

import pytest

from creditdash.errors import LlmError
from creditdash.extract import PROMPT_VERSION, extract
from creditdash.models import Extraction, Instrument
from creditdash.resolve import resolve
from tests.conftest import FIXTURES

PERIODIC = "0001769628-26-000366"
K1, K2 = "0001769628-26-000357", "0001769628-26-000432"

CANNED = Extraction(period_end=date(2026, 6, 30), unit_note="$ millions", instruments=[
    Instrument(name="DDTL 1.0 Facility", kind="delayed_draw_term_loan", seniority_class="parent_secured",
               principal=1300, maturity="2028-03", source=PERIODIC, quotes=["DDTL 1.0 Facility"]),
    Instrument(name="DDTL 4.0 Facility", kind="delayed_draw_term_loan", seniority_class="asset_level_secured",
               principal=2837, maturity="2032-03", source=PERIODIC, quotes=["DDTL 4.0 Facility"]),
])


class FakeAsk:
    def __init__(self, result=CANNED, error=None):
        self.calls = []
        self.result, self.error = result, error

    def __call__(self, prompt, system, schema, *, settings, **kw):
        self.calls.append({"prompt": prompt, "system": system, "schema": schema})
        if self.error:
            raise self.error
        return self.result, {"cached": False}


@pytest.fixture
def resolved(edgar):
    return resolve("CRWV", edgar)


@pytest.fixture(scope="module")
def instance_xml():
    return (FIXTURES / "instance.xml").read_text()


@pytest.fixture(scope="module")
def eight_ks():
    return {K1: (FIXTURES / "8k-20260807.htm").read_text(), K2: (FIXTURES / "8k-20260917.htm").read_text()}


def no_html():
    raise AssertionError("filing HTML must not be loaded when the instance has a debt text block")


def test_prompt_contains_note_and_8ks(resolved, instance_xml, eight_ks, settings):
    ask = FakeAsk()
    ext, reason, sources = extract(resolved, instance_xml, no_html, eight_ks, ask=ask, settings=settings)
    assert ext == CANNED and reason is None
    prompt = ask.calls[0]["prompt"]
    assert "DDTL 4.0 Facility" in prompt and f"## 8-K {K1}" in prompt and f"## 8-K {K2}" in prompt
    assert f"## Periodic filing 10-Q {PERIODIC}, period end 2026-06-30" in prompt
    assert set(sources) == {PERIODIC, K1, K2}
    assert "DDTL 4.0 Facility" in sources[PERIODIC] and sources[K1].startswith("Item 1.01")
    assert ask.calls[0]["schema"] is Extraction


def test_system_prompt_rules(resolved, instance_xml, eight_ks, settings):
    ask = FakeAsk()
    extract(resolved, instance_xml, no_html, eight_ks, ask=ask, settings=settings)
    system = ask.calls[0]["system"]
    assert "$ millions" in system and "recorded once" in system and "verbatim" in system
    assert "annual filing" in system and PROMPT_VERSION == "extract_v6"


ANNUAL = "0001769628-26-000104"


def test_annual_note_added_for_detail(resolved, instance_xml, eight_ks, settings):
    ask = FakeAsk()
    annual = (FIXTURES / "instance-10k.xml").read_text()
    _, _, sources = extract(resolved, instance_xml, no_html, eight_ks, annual_instance=annual, ask=ask,
                            settings=settings)
    prompt = ask.calls[0]["prompt"]
    periodic_at, annual_at = prompt.index(f"## Periodic filing 10-Q {PERIODIC}"), prompt.index(
        f"## Annual filing 10-K {ANNUAL}, period end 2025-12-31")
    assert periodic_at < annual_at < prompt.index(f"## 8-K {K1}")
    assert set(sources) == {PERIODIC, ANNUAL, K1, K2} and "DDTL 3.0 Facility" in sources[ANNUAL]


def test_annual_note_alone_when_the_10q_has_none(resolved, eight_ks, settings):
    ask = FakeAsk()
    annual = (FIXTURES / "instance-10k.xml").read_text()
    ext, reason, sources = extract(resolved, "<xbrl/>", lambda: "<p>nothing</p>", eight_ks, annual_instance=annual,
                                   ask=ask, settings=settings)
    assert ext == CANNED and reason is None and PERIODIC not in sources
    assert "has no debt note" in ask.calls[0]["prompt"]


def test_no_footnote_skips(resolved, eight_ks, settings):
    ask = FakeAsk()
    ext, reason, _ = extract(resolved, "<xbrl/>", lambda: "<p>nothing</p>", eight_ks, ask=ask, settings=settings)
    assert (ext, reason) == (None, "debt footnote not found") and ask.calls == []


def test_html_fallback_used(resolved, eight_ks, settings):
    ask = FakeAsk()
    html = "<p><b>Note 9. Debt</b></p>" + "<p>The Term Loan matures in 2029 and bears 7% interest.</p>" * 8
    ext, reason, sources = extract(resolved, "<xbrl/>", lambda: html, eight_ks, ask=ask, settings=settings)
    assert ext == CANNED and sources[PERIODIC].startswith("Note 9. Debt")


def test_llm_failure_skips(resolved, instance_xml, eight_ks, settings):
    ask = FakeAsk(error=LlmError("boom"))
    ext, reason, sources = extract(resolved, instance_xml, no_html, eight_ks, ask=ask, settings=settings)
    assert (ext, reason) == (None, "LLM failed: boom") and PERIODIC in sources


def test_6k_after_the_period_is_sent_as_text(resolved, instance_xml, settings):
    from creditdash.models import Filing
    sixk = Filing(form="6-K", accession="0001104659-26-112824", filed=date(2026, 10, 1), primary_doc="x.htm")
    r = resolved.model_copy(update={"debt_8ks": [sixk]})
    ask = FakeAsk()
    _, _, sources = extract(r, instance_xml, no_html, {sixk.accession: "Nebius issued $1.0 billion aggregate "
                            "principal amount of notes."}, ask=ask, settings=settings)
    assert "## 6-K 0001104659-26-112824 filed 2026-10-01" in ask.calls[0]["prompt"]
    assert sources[sixk.accession].startswith("Nebius issued")
