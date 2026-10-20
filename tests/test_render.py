import re

from creditdash.models import Dashboard
from creditdash.render import SECTION_IDS, render, write_outputs
from tests.sample import sample_dashboard
from tests.toy import TOY

SECTIONS = ["kpis", "stack", "yield", "paying", "wall", "coverage", "raise", "waterfall", "instruments", "method"]


def embedded(html: str) -> str:
    m = re.search(r'<script id="dashboard-data" type="application/json">(.*?)</script>', html, re.S)
    assert m, "no embedded dashboard data"
    return m.group(1)


def test_all_sections():
    html = render(sample_dashboard())
    assert SECTION_IDS == SECTIONS
    positions = [html.index(f'id="{s}"') for s in SECTIONS]
    assert positions == sorted(positions)


def test_embedded_json_roundtrips():
    d = sample_dashboard()
    assert Dashboard.model_validate_json(embedded(render(d))) == d


def test_offline():
    html = render(sample_dashboard())
    assert not re.search(r"<script[^>]+src=", html) and not re.search(r"<link[^>]+href=", html)
    assert "d3js.org v7.9.0" in html


def test_script_injection():
    evil = "</script><img src=x onerror=alert(1)>"
    toy = [TOY[0].model_copy(update={"name": evil})] + TOY[1:]
    d = sample_dashboard(instruments=toy)
    html = render(d)
    assert "</script><img" not in html and "<img src=x" not in html
    assert Dashboard.model_validate_json(embedded(html)).instruments[0].name == evil


def test_company_name_escaped():
    d = sample_dashboard()
    d = d.model_copy(update={"company": d.company.model_copy(update={"name": "<b>Evil & Co</b>"})})
    html = render(d)
    assert "<b>Evil" not in html and "&lt;b&gt;Evil &amp; Co&lt;/b&gt;" in html


def test_skipped_banner():
    html = render(sample_dashboard(extraction_skipped="--no-llm"))
    assert "instrument detail unavailable: --no-llm" in html


CHARTS = ["drawStack", "drawYield", "drawPaying", "drawWall", "drawCoverage", "drawRaise", "drawWaterfall"]


def test_chart_hooks():
    html = render(sample_dashboard())
    for fn in CHARTS:
        assert f"function {fn}(" in html, fn
        assert f"{fn}()" in html, fn


def test_write_outputs(tmp_path):
    path = write_outputs(sample_dashboard(), tmp_path)
    assert path == tmp_path / "CRWV" / "2026-06-30" / "index.html"
    assert path.read_text().startswith("<!doctype html>")
    assert Dashboard.model_validate_json((path.parent / "dashboard.json").read_text()).company.ticker == "CRWV"


def test_unmodeled_interest_banner():
    d = sample_dashboard()
    d = d.model_copy(update={"model": d.model.model_copy(update={"excluded": ["B (no rate: interest not modeled)"]})})
    assert "Interest not modeled for: B" in render(d)


def test_basis_lines_shown_as_info():
    d = sample_dashboard().model_copy(update={"basis": ["Full-year figures come from the 20-F for fiscal 2025.",
                                                          "Balances are as of 2026-06-30 from 6-K <x>."]})
    html = render(d)
    assert html.count('class="cw-info"') == 2 and "6-K &lt;x&gt;" in html
