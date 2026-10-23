import asyncio
import json
from dataclasses import replace

import pytest
from mcp import Client
from mcp.client.extension import advertise

from creditdash.errors import UnknownTicker
from creditdash.market import fetch_market, load_overrides
from creditdash.mcp_server import VIEW_URI, create_server
from creditdash.models import Fact
from creditdash.render import write_outputs
from creditdash.snapshot import snapshot
from tests.sample import sample_dashboard
from tests.test_market import Market

APPS = advertise("io.modelcontextprotocol/ui", {"mimeTypes": ["text/html;profile=mcp-app"]})


class FakeBuild:
    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, ticker, *, settings, progress, **kw):
        self.calls.append({"ticker": ticker, **kw})
        for stage in ("resolve", "fetch", "render"):
            progress(stage)
        if ticker == "ZZZZZ":
            raise UnknownTicker("Unknown ticker ZZZZZ: not in the SEC's company list.")
        return write_outputs(sample_dashboard(ticker=ticker), settings.out)


@pytest.fixture(autouse=True)
def in_tmp(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # overrides/ is relative


@pytest.fixture
def opened():
    return []


@pytest.fixture
def build():
    return FakeBuild()


def call(server, tool, args=None, *, apps=False, progress=None):
    async def go():
        async with Client(server, extensions=[APPS] if apps else None) as c:
            return await c.call_tool(tool, args or {}, progress_callback=progress)
    return asyncio.run(go())


def make(settings, build, opened, **kw):
    return create_server(settings_fn=lambda: replace(settings, **kw), build_fn=build,
                         open_browser=lambda url: opened.append(url) or True)


def test_snapshot_is_compact_json(settings):
    s = snapshot(sample_dashboard())
    json.dumps(s)
    assert s["company"]["ticker"] == "CRWV" and s["filing"]["period_end"] == "2026-06-30"
    assert s["can_it_pay_interest"]["bottom_line"] and len(s["can_it_pay_interest"]["tests"]) == 4
    assert {"name", "seniority", "principal", "rate", "maturity", "verified"} <= set(s["instruments"][0])
    assert "debt_service" not in json.dumps(s)  # the monthly schedules stay on the page


def test_credit_snapshot_builds_and_reports_progress(settings, build, opened):
    steps = []

    async def progress(p, total, message):
        steps.append((p, total, message))

    r = call(make(settings, build, opened), "credit_snapshot", {"ticker": "crwv"}, progress=progress)
    assert not r.is_error
    out = r.structured_content
    assert out["dashboard"]["ticker"] == "CRWV" and out["dashboard"]["period"] == "2026-06-30"
    assert out["can_it_pay_interest"]["tests"] and "notes" not in out
    assert build.calls[0]["no_llm"] is False
    assert [m for _, _, m in steps] == ["CRWV: resolve", "CRWV: fetch", "CRWV: render", "CRWV: done"]


def test_no_key_builds_sec_totals_and_says_so(settings, build, opened):
    r = call(make(settings, build, opened, llm_api_key=None), "credit_snapshot", {"ticker": "CRWV"})
    assert build.calls[0]["no_llm"] is True
    assert "LLM_API_KEY" in r.structured_content["notes"][0]


@pytest.mark.parametrize("ticker, message", [("ZZZZZ", "Unknown ticker ZZZZZ"), ("not a ticker!", "is not a ticker")])
def test_errors_reach_the_model(settings, build, opened, ticker, message):
    r = call(make(settings, build, opened), "credit_snapshot", {"ticker": ticker})
    assert r.is_error and message in r.content[0].text


def test_missing_user_agent_says_where_to_set_it(settings, build, opened):
    r = call(make(settings, build, opened, sec_user_agent=""), "credit_snapshot", {"ticker": "CRWV"})
    assert r.is_error and "SEC_USER_AGENT" in r.content[0].text and "Extensions" in r.content[0].text


def test_show_dashboard_opens_the_browser_without_apps(settings, build, opened):
    r = call(make(settings, build, opened), "show_dashboard", {"ticker": "CRWV"})
    assert not r.is_error and len(build.calls) == 1  # nothing built yet, so it builds
    assert r.structured_content["opened_in_browser"] and not r.structured_content["shown_in_chat"]
    assert opened == [r.structured_content["url"]] and opened[0].startswith("file://")


def test_show_dashboard_in_chat_with_apps(settings, build, opened):
    write_outputs(sample_dashboard(ticker="CRWV"), settings.out)
    server = make(settings, build, opened)
    r = call(server, "show_dashboard", {"ticker": "CRWV"}, apps=True)
    assert r.structured_content == {**r.structured_content, "ticker": "CRWV", "period": "2026-06-30",
                                    "shown_in_chat": True, "opened_in_browser": False}
    assert not build.calls and not opened
    page = call(server, "dashboard_html", {"ticker": "CRWV", "period": "2026-06-30"}, apps=True)
    assert 'id="dashboard-data"' in page.content[0].text


def test_show_dashboard_unknown_period_lists_built(settings, build, opened):
    write_outputs(sample_dashboard(ticker="CRWV"), settings.out)
    r = call(make(settings, build, opened), "show_dashboard", {"ticker": "CRWV", "period": "2020-01-01"})
    assert r.is_error and "Built periods: 2026-06-30" in r.content[0].text


def test_dashboard_html_is_only_for_the_viewer(settings, build, opened):
    write_outputs(sample_dashboard(ticker="CRWV"), settings.out)
    server = make(settings, build, opened)
    r = call(server, "dashboard_html", {"ticker": "CRWV", "period": "2026-06-30"})
    assert r.is_error and "show_dashboard" in r.content[0].text

    async def tools():
        async with Client(server) as c:
            return {t.name: t for t in (await c.list_tools()).tools}
    t = asyncio.run(tools())
    assert t["dashboard_html"].meta["ui"]["visibility"] == ["app"]
    assert t["show_dashboard"].meta["ui"]["resourceUri"] == VIEW_URI


def test_viewer_resource(settings, build, opened):
    async def read():
        async with Client(make(settings, build, opened)) as c:
            return (await c.read_resource(VIEW_URI)).contents[0]
    res = asyncio.run(read())
    assert res.mime_type == "text/html;profile=mcp-app"
    assert res.text.count("</script>") == 1  # the injected size script cannot close the shell's script early
    assert "ui/initialize" in res.text and "dashboard_html" in res.text


def test_set_market_inputs_merges_and_feeds_market(settings, build, opened, tmp_path):
    server = make(settings, build, opened)
    call(server, "set_market_inputs", {"ticker": "CRWV", "cds_5y_bps": 650,
                                       "bond_yields": {"2030 Senior Notes": 14.2, "2031 Notes": 12.0},
                                       "consensus": {"2027": {"revenue": 26311, "ebitda": 16635}}})
    r = call(server, "set_market_inputs", {"ticker": "CRWV", "price": 62.36, "bond_yields": {"2031 Notes": None}})
    assert not r.is_error and "credit_snapshot" in r.structured_content["next"]
    o = load_overrides("CRWV", tmp_path / "overrides")
    assert o == {"cds_5y_bps": 650, "bond_yields": {"2030 Senior Notes": 14.2},
                 "consensus": {2027: {"revenue": 26311, "ebitda": 16635}}, "price": 62.36}
    m = fetch_market("CRWV", Fact(value=100), tmp_path / "overrides", transport=Market())
    assert m.cds_bps.value == 650 and m.consensus[0].fiscal_year == 2027 and m.price.value == 62.36


def test_list_dashboards(settings, build, opened):
    write_outputs(sample_dashboard(ticker="CRWV"), settings.out)
    write_outputs(sample_dashboard(ticker="NBIS"), settings.out)
    r = call(make(settings, build, opened), "list_dashboards")
    rows = r.structured_content["result"]
    assert [(x["ticker"], x["period"], x["company"]) for x in rows] == [
        ("CRWV", "2026-06-30", "Toy Compute, Inc."), ("NBIS", "2026-06-30", "Toy Compute, Inc.")]


def test_compare_credit_builds_both_and_names_the_stronger(settings, build, opened):
    r = call(make(settings, build, opened), "compare_credit", {"ticker_a": "crwv", "ticker_b": "NBIS"})
    assert not r.is_error, r.content
    out = r.structured_content
    assert [c["ticker"] for c in out["companies"]] == ["CRWV", "NBIS"]
    assert [x["ticker"] for x in build.calls] == ["CRWV", "NBIS"]
    assert {"repayment", "adequacy", "tightness"} == set(out["tally"])
    assert all("CRWV" in m and "NBIS" in m and "stronger" in m for m in out["measures"])
    assert out["comparison"]["path"].endswith("_compare/CRWV_vs_NBIS/index.html")


def test_show_comparison_in_chat_with_apps(settings, build, opened):
    write_outputs(sample_dashboard(ticker="CRWV"), settings.out)
    server = make(settings, build, opened)
    r = call(server, "show_comparison", {"ticker_a": "CRWV", "ticker_b": "NBIS"}, apps=True)
    assert not r.is_error and [x["ticker"] for x in build.calls] == ["NBIS"]  # CRWV was built already
    assert r.structured_content["ticker_a"] == "CRWV" and r.structured_content["shown_in_chat"] and not opened
    page = call(server, "comparison_html", {"ticker_a": "CRWV", "ticker_b": "NBIS"}, apps=True)
    assert 'id="comparison-data"' in page.content[0].text
    assert call(server, "comparison_html", {"ticker_a": "CRWV", "ticker_b": "NBIS"}).is_error  # viewer only


def test_viewer_loads_comparisons():
    from creditdash.mcp_server import VIEW_HTML
    assert "comparison_html" in VIEW_HTML and "ticker_a" in VIEW_HTML
