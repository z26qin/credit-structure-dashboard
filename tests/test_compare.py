import json
import math
import re

from creditdash.compare import NET_CASH, NEVER, _runway, compare, stronger, summary
from creditdash.models import Comparison, Fact
from creditdash.render import COMPARE_SECTION_IDS, render_comparison, write_comparison
from tests.sample import sample_dashboard
from tests.toy import financials

SECTIONS = ["verdict", "tests", "coverage-path", "adequacy", "quarters", "runway", "maturity", "measures", "method"]


def pair():
    """The toy company (TTM EBITDA 500) against a copy that earns 900."""
    return sample_dashboard(ticker="AAA"), sample_dashboard(ticker="BBB", financials=financials(ttm_ebitda=900.0))


def measure(c: Comparison, key: str):
    return next(m for m in c.measures if m.key == key)


def test_stronger_follows_direction_and_ties_within_five_percent():
    assert stronger(2.0, "", 1.0, "", "higher") == "a"
    assert stronger(2.0, "", 1.0, "", "lower") == "b"
    assert stronger(-4.8, "", -20.2, "", "higher") == "a"  # less negative is stronger
    assert stronger(1.00, "", 1.04, "", "higher") == "even"
    assert stronger(None, NEVER, 30.0, "", "higher") == "a"  # outlasts the model
    assert stronger(None, NET_CASH, 2.0, "", "lower") == "a"
    assert stronger(None, NEVER, None, NEVER, "higher") == "even"
    assert stronger(None, "no capex reported", 3.0, "", "higher") is None
    assert stronger(1.0, "", 2.0, "", None) is None  # context rows are not compared


def test_runway_months():
    assert _runway(100.0, [(12, -60.0), (12, -60.0)]) == (20.0, "")  # 12 months, then 40 of the next 60
    assert _runway(100.0, [(6, 10.0), (12, 50.0)]) == (None, NEVER)
    assert _runway(100.0, []) == (None, "no capex reported")
    assert _runway(None, [(12, -1.0)]) == (None, "no liquidity figure")
    assert _runway(0.0, [(12, 5.0)]) == (0.0, "")


def test_compare_names_the_stronger_side_and_tallies():
    a, b = pair()
    c = compare(a, b)
    earnings = measure(c, "ebitda_to_next12m_interest")
    assert math.isclose(earnings.b / earnings.a, 900 / 500) and earnings.stronger == "b"
    assert measure(c, "net_debt_to_ebitda").stronger == "b"
    assert measure(c, "cfo_to_debt").stronger == "even"  # same cash flow and debt
    assert measure(c, "revenue").stronger is None
    assert c.tally["repayment"]["b"] >= 2 and set(c.tally) == {"repayment", "adequacy", "tightness"}
    assert sum(c.tally["adequacy"].values()) == sum(1 for m in c.measures if m.section == "adequacy" and m.stronger)
    assert c.scenarios[0] == "ttm" and "consensus" not in c.scenarios
    assert not any(m.section == "market" for m in c.measures)  # neither side has a CDS or bond yield


def test_paths_are_dated_and_normalized():
    a, b = pair()
    c = compare(a, b)
    due = [p.due_cumulative for p in c.a.path]
    assert all(x <= y + 1e-9 for x, y in zip(due, due[1:])) and due[-1] <= 1 + 1e-9
    assert [p.end for p in c.a.path] == [p.end for p in a.model.periods]
    assert c.a.path[0].icr_refi["ttm"] == next(r.icr_refi for r in a.model.coverage
                                               if r.scenario == "ttm" and r.period == c.a.path[0].period)
    liquidity = a.model.headline.liquidity
    first = a.model.raise_rows[0]
    assert math.isclose(c.a.path[0].liquidity_paid, (liquidity + first.cfo - first.capex - first.principal) / liquidity)


def test_net_cash_and_period_gap():
    a, b = pair()
    rich = financials()
    rich = rich.model_copy(update={"cash": Fact(value=5000.0), "period_end": rich.period_end.replace(month=3, day=31)})
    c = compare(a, sample_dashboard(ticker="CCC", financials=rich))
    m = measure(c, "net_debt_to_ebitda")
    assert m.b is None and m.b_note == NET_CASH and m.stronger == "b"
    assert any("Period ends differ by 91 days" in n for n in c.notes)


def test_summary_is_compact_and_names_tickers():
    c = compare(*pair())
    s = summary(c)
    json.dumps(s)
    assert [x["ticker"] for x in s["companies"]] == ["AAA", "BBB"] and len(s["companies"][0]["tests"]) == 4
    row = next(m for m in s["measures"] if m["label"] == "Earnings ÷ interest")
    assert row["stronger"] == "BBB" and {"AAA", "BBB", "how"} <= set(row)
    assert set(s["tally"]["repayment"]) == {"AAA", "BBB", "even"}
    assert "path" not in json.dumps(s)  # the series stay on the page


def embedded(html: str) -> str:
    m = re.search(r'<script id="comparison-data" type="application/json">(.*?)</script>', html, re.S)
    assert m, "no embedded comparison data"
    return m.group(1)


def test_render_comparison_sections_data_and_offline():
    c = compare(*pair())
    html = render_comparison(c)
    assert COMPARE_SECTION_IDS == SECTIONS
    positions = [html.index(f'id="{s}"') for s in SECTIONS]
    assert positions == sorted(positions)
    assert Comparison.model_validate_json(embedded(html)) == c
    assert not re.search(r"<script[^>]+src=", html) and "d3js.org v7.9.0" in html
    for fn in ("drawTests", "drawCoverage", "drawAdequacy", "drawQuarters", "drawRunway", "drawMaturity", "drawTable"):
        assert f"function {fn}(" in html and f"{fn}()" in html, fn
    assert "window.CW" in html  # the shared helpers are inlined before the page script


def test_comparison_escapes_names():
    a, b = pair()
    a = a.model_copy(update={"company": a.company.model_copy(update={"name": "</script><img src=x onerror=alert(1)>"})})
    html = render_comparison(compare(a, b))
    assert "</script><img" not in html and "<img src=x" not in html


def test_write_comparison(tmp_path):
    path = write_comparison(compare(*pair()), tmp_path)
    assert path == tmp_path / "_compare" / "AAA_vs_BBB" / "index.html"
    assert Comparison.model_validate_json((path.parent / "comparison.json").read_text()).b.ticker == "BBB"
