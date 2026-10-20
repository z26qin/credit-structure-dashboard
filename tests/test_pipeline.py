from datetime import date

import httpx
import pytest

from creditdash import cli, pipeline
from creditdash.errors import UnknownTicker
from creditdash.models import Dashboard, Extraction, Instrument, ReconStatus
from tests.test_market import Market

PERIODIC = "0001769628-26-000366"
EXTRACTION = Extraction(period_end=date(2026, 6, 30), unit_note="$ millions", instruments=[
    Instrument(name="DDTL 1.0 Facility", kind="delayed_draw_term_loan", seniority_class="asset_level_secured",
               principal=1300, rate_type="unknown", effective_rate_pct=15, maturity="2028-03",
               amortization="amortizing", source=PERIODIC, quotes=["| DDTL 1.0 Facility | Mar 2028 | 15% | $1,300 |"]),
    Instrument(name="2030 Senior Notes", kind="senior_notes", seniority_class="senior_unsecured", principal=2000,
               rate_type="fixed", fixed_rate_pct=9.25, maturity="2030-06", amortization="bullet", source=PERIODIC,
               quotes=["| 2030 Senior Notes | Jun 2030 | 10% | 2,000 |"]),
])


class Llm(httpx.MockTransport):
    def __init__(self):
        self.calls = 0

        def handle(r):
            self.calls += 1
            return httpx.Response(200, json={
                "choices": [{"message": {"content": EXTRACTION.model_dump_json()}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 9000, "completion_tokens": 900}})
        super().__init__(handle)


@pytest.fixture
def transports(sec_transport):
    return {"sec_transport": sec_transport, "llm_transport": Llm(), "market_transport": Market()}


def build(settings, transports, tmp_path, **kw):
    seen = []
    path = pipeline.build("CRWV", settings=settings, progress=seen.append, overrides_dir=tmp_path / "overrides",
                          **transports, **kw)
    return path, seen


def test_build_writes_outputs(settings, transports, tmp_path):
    path, seen = build(settings, transports, tmp_path)
    assert path == settings.out / "CRWV" / "2026-06-30" / "index.html" and path.exists()
    d = Dashboard.model_validate_json((path.parent / "dashboard.json").read_text())
    assert seen == list(pipeline.STAGES)
    assert d.financials.total_debt.value == 35551.0 and d.market.sofr_pct.value == 3.87
    assert [i.name for i in d.extraction.instruments] == ["DDTL 1.0 Facility", "2030 Senior Notes"]
    assert d.reconciliation.unverified == [] and d.reconciliation.status == ReconStatus.fail  # 2 of ~19 instruments
    assert d.instruments[-1].name == "Unallocated"
    assert d.settings["llm_model"] == "deepseek-flash" and d.settings["prompt_version"] == "extract_v6"
    assert transports["llm_transport"].calls == 1


def test_latest_10k_read_for_detail(settings, transports, tmp_path):
    path, _ = build(settings, transports, tmp_path)
    annual_url = "https://www.sec.gov/Archives/edgar/data/1769628/000176962826000104/crwv-20251231_htm.xml"
    assert transports["sec_transport"].calls.count(annual_url) == 1
    d = Dashboard.model_validate_json((path.parent / "dashboard.json").read_text())
    assert d.settings["prompt_version"] == "extract_v6"
    assert d.financials.maturities_as_of == date(2026, 6, 30)


def test_rebuild_is_cached(settings, transports, tmp_path):
    build(settings, transports, tmp_path)
    before = len(transports["sec_transport"].calls)
    build(settings, transports, tmp_path)
    assert transports["sec_transport"].calls[before:] == ["https://data.sec.gov/submissions/CIK0001769628.json"]
    assert transports["llm_transport"].calls == 1


def test_refresh_refetches(settings, transports, tmp_path):
    build(settings, transports, tmp_path)
    build(settings, transports, tmp_path, refresh=True)
    assert transports["llm_transport"].calls == 2


def test_no_llm(settings, transports, tmp_path):
    path, _ = build(settings, transports, tmp_path, no_llm=True)
    d = Dashboard.model_validate_json((path.parent / "dashboard.json").read_text())
    assert d.extraction_skipped == "--no-llm" and d.extraction is None
    assert d.reconciliation.status == ReconStatus.skipped and transports["llm_transport"].calls == 0
    assert "instrument detail unavailable: --no-llm" in path.read_text()


def test_missing_llm_key_still_builds(settings, transports, tmp_path):
    from tests.conftest import with_settings
    path, _ = build(with_settings(settings, llm_api_key=None), transports, tmp_path)
    d = Dashboard.model_validate_json((path.parent / "dashboard.json").read_text())
    assert d.extraction_skipped.startswith("LLM failed: LLM_API_KEY")


def test_cli_unknown_ticker_exit_2(monkeypatch, capsys):
    def boom(*a, **kw):
        raise UnknownTicker("Unknown ticker ZZZZZ: not in the SEC's company list")
    monkeypatch.setattr(cli.pipeline, "build", boom)
    assert cli.main(["build", "ZZZZZ"]) == 2
    assert "ZZZZZ" in capsys.readouterr().err


def test_cli_list(settings, transports, tmp_path, monkeypatch, capsys):
    build(settings, transports, tmp_path)
    monkeypatch.setenv("CREDITDASH_OUT", str(settings.out))
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "CRWV" in out and "2026-06-30" in out
