"""20-F filers end to end: NBIS's FY2025 20-F plus the 6-K with its June 2026 interim statements."""
import json
from datetime import date

import httpx
import pytest
from pytest import approx

from creditdash import pipeline
from creditdash.errors import UnsupportedFiler
from creditdash.interim import InterimStatements
from creditdash.models import Dashboard, Extraction, Instrument
from tests.test_interim import ARCHIVE, INTERIM, NBIS, NBIS_FILES, statements
from tests.test_market import Market

ANNUAL = "0001104659-26-052948"
FILES = NBIS_FILES | {
    "https://data.sec.gov/api/xbrl/companyfacts/CIK0001513845.json": (NBIS / "companyfacts.json").read_bytes(),
    f"{ARCHIVE}/000110465926052948/nbis-20251231x20f_htm.xml": (NBIS / "instance-20f.xml").read_bytes(),
}
QUOTE = "| 1.25% Convertible Senior Notes due March 2031 | 4,696.5 |"
EXTRACTION = Extraction(period_end=date(2026, 6, 30), unit_note="$ millions", instruments=[
    Instrument(name="1.25% Convertible Senior Notes due March 2031", kind="convertible_notes",
               seniority_class="convertible", principal=2590, rate_type="fixed", fixed_rate_pct=1.25,
               maturity="2031-03", amortization="bullet", source=INTERIM, quotes=[QUOTE])])


class Llm(httpx.MockTransport):
    """Answers the interim-statements request and the instrument request, told apart by the schema asked for."""

    def __init__(self, interim_json: str):
        self.calls = []

        def handle(r):
            body = json.loads(r.content)
            system = body["messages"][0]["content"]
            kind = "interim" if '"title": "InterimStatements"' in system else "instruments"
            self.calls.append((kind, body["messages"][1]["content"]))
            content = interim_json if kind == "interim" else EXTRACTION.model_dump_json()
            return httpx.Response(200, json={"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                                             "usage": {"prompt_tokens": 5000, "completion_tokens": 900}})
        super().__init__(handle)


@pytest.fixture
def interim_json():
    from creditdash.interim import statements_text
    return statements(statements_text((NBIS / "6k-20260812-ex99d2.htm").read_text())).model_dump_json()


def build(settings, tmp_path, llm, files=FILES, **kw):
    from tests.conftest import FixtureTransport
    path = pipeline.build("NBIS", settings=settings, overrides_dir=tmp_path / "o", sec_transport=FixtureTransport(files),
                          llm_transport=llm, market_transport=Market(), **kw)
    return Dashboard.model_validate_json((path.parent / "dashboard.json").read_text()), path


def test_20f_with_interim_6k(settings, tmp_path, interim_json):
    llm = Llm(interim_json)
    d, path = build(settings, tmp_path, llm)
    assert (d.filing.form, d.filing.accession, d.financials.period_end) == ("6-K", INTERIM, date(2026, 6, 30))
    f = d.financials
    assert f.total_debt.value == approx(8545.7) and f.total_debt.tag.startswith("6-K " + INTERIM)
    assert f.cash.value == 8042.1 and f.ttm.label == "LTM Q2 26" and f.ttm.revenue == approx(1355.1)
    assert [k for k, _ in llm.calls] == ["interim", "instruments"]
    instruments_prompt = llm.calls[1][1]
    assert f"## Periodic filing 6-K {INTERIM}, period end 2026-06-30" in instruments_prompt
    assert f"## Annual filing 20-F {ANNUAL}, period end 2025-12-31" in instruments_prompt
    assert any("20-F" in b for b in d.basis) and any(INTERIM in b for b in d.basis)
    assert d.settings["interim_prompt_version"] == "interim_v2"
    assert path.parent.name == "2026-06-30"


def test_6ks_between_period_end_and_interim_filing_are_read(settings, tmp_path, interim_json):
    # The interim 6-K is filed 2026-08-12; a loan announced on 2026-07-17 is still after the period end.
    files = FILES | {
        f"{ARCHIVE}/000110465926084452/index.json": json.dumps({"directory": {"item": [
            {"name": "tm2620683d1_6k.htm"}]}}).encode(),
        f"{ARCHIVE}/000110465926084452/tm2620683d1_6k.htm":
            b"<p>Nebius Compute II entered into a $775 million term loan facility.</p>",
    }
    llm = Llm(interim_json)
    build(settings, tmp_path, llm, files=files)
    assert "## 6-K 0001104659-26-084452 filed 2026-07-17" in llm.calls[1][1]


def test_20f_annual_only_without_the_model(settings, tmp_path, interim_json):
    llm = Llm(interim_json)
    d, _ = build(settings, tmp_path, llm, no_llm=True)
    assert (d.filing.form, d.filing.accession, d.financials.ttm.label) == ("20-F", ANNUAL, "FY25")
    assert llm.calls == [] and any("--no-llm" in b for b in d.basis)


def test_interim_figures_not_in_the_text_are_dropped(settings, tmp_path, interim_json):
    bad = InterimStatements.model_validate_json(interim_json)
    bad.cash.value = 1.0
    d, _ = build(settings, tmp_path, Llm(bad.model_dump_json()))
    assert d.financials.cash.note.startswith("from the 20-F") and any("cash" in b for b in d.basis)


def test_ifrs_20f_filer_is_explained(settings, tmp_path, interim_json):
    ifrs = json.loads((NBIS / "companyfacts.json").read_text())
    ifrs["facts"] = {"ifrs-full": {"Revenue": {"units": {"USD": []}}}}
    files = FILES | {"https://data.sec.gov/api/xbrl/companyfacts/CIK0001513845.json": json.dumps(ifrs).encode()}
    with pytest.raises(UnsupportedFiler, match="IFRS"):
        build(settings, tmp_path, Llm(interim_json), files=files)
