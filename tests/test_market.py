import httpx
import pytest

from creditdash.market import fetch_market, load_overrides
from creditdash.models import ConsensusYear, Fact

SHARES = Fact(value=551_536_602, tag="dei:EntityCommonStockSharesOutstanding")
SOFR_BODY = {"refRates": [{"effectiveDate": "2026-10-19", "type": "SOFR", "percentRate": 3.87}]}
YAHOO_BODY = {"chart": {"result": [{"meta": {"regularMarketPrice": 62.355, "regularMarketTime": 1792509322,
                                             "currency": "USD"}}]}}


class Market(httpx.MockTransport):
    def __init__(self, yahoo_status=200, sofr_status=200):
        self.calls: list[str] = []

        def handle(r: httpx.Request) -> httpx.Response:
            self.calls.append(str(r.url))
            if r.url.host == "markets.newyorkfed.org":
                return httpx.Response(sofr_status, json=SOFR_BODY)
            if r.url.host == "query1.finance.yahoo.com":
                return httpx.Response(yahoo_status, json=YAHOO_BODY)
            return httpx.Response(404)
        super().__init__(handle)


def test_sofr(tmp_path):
    m = fetch_market("CRWV", SHARES, tmp_path, transport=Market())
    assert (m.sofr_pct.value, m.sofr_pct.period) == (3.87, "2026-10-19")


def test_market_cap(tmp_path):
    m = fetch_market("CRWV", SHARES, tmp_path, transport=Market())
    assert m.price.value == 62.355 and round(m.market_cap.value, 1) == 34391.1
    assert m.ebitda_label == "EBITDA (operating income + D&A)"


def test_override_price_skips_yahoo(tmp_path):
    (tmp_path / "CRWV.yaml").write_text("price: 60\n")
    t = Market()
    m = fetch_market("CRWV", SHARES, tmp_path, transport=t)
    assert m.price.value == 60 and m.price.tag == "override"
    assert not any("yahoo" in c for c in t.calls)


def test_yahoo_down(tmp_path):
    m = fetch_market("CRWV", SHARES, tmp_path, transport=Market(yahoo_status=500))
    assert m.price.value is None and "yahoo" in m.price.note.lower()
    assert m.market_cap.value is None and m.sofr_pct.value == 3.87


def test_no_shares_no_market_cap(tmp_path):
    m = fetch_market("CRWV", Fact(value=None), tmp_path, transport=Market())
    assert m.price.value == 62.355 and m.market_cap.value is None


def test_overrides_consensus_and_more(tmp_path):
    (tmp_path / "CRWV.yaml").write_text(
        "consensus:\n  2027: {ebitda: 16635, capex: 47029}\n  2026: {ebitda: 7571}\n"
        "ratings: {DDTL 4.0 Facility: A3}\nbond_yields: {2032 9.625% Senior Notes: 14.2}\n"
        "ebitda_label: adj. EBITDA\n")
    m = fetch_market("CRWV", SHARES, tmp_path, transport=Market())
    assert m.consensus == [ConsensusYear(fiscal_year=2026, ebitda=7571),
                           ConsensusYear(fiscal_year=2027, ebitda=16635, capex=47029)]
    assert m.ratings == {"DDTL 4.0 Facility": "A3"} and m.bond_yields == {"2032 9.625% Senior Notes": 14.2}
    assert m.ebitda_label == "adj. EBITDA"


def test_no_overrides_file(tmp_path):
    assert load_overrides("CRWV", tmp_path) == {}


def test_bad_overrides_file(tmp_path):
    (tmp_path / "CRWV.yaml").write_text("price: [not, a, number\n")
    with pytest.raises(Exception, match="CRWV.yaml"):
        load_overrides("CRWV", tmp_path)


def test_sofr_cached_and_reused_when_down(tmp_path):
    fetch_market("CRWV", SHARES, tmp_path, transport=Market(), cache_dir=tmp_path / "cache")
    m = fetch_market("CRWV", SHARES, tmp_path, transport=Market(sofr_status=500), cache_dir=tmp_path / "cache")
    assert m.sofr_pct.value == 3.87 and "cached" in m.sofr_pct.note


def test_sofr_override(tmp_path):
    (tmp_path / "CRWV.yaml").write_text("sofr: 4.1\n")
    m = fetch_market("CRWV", SHARES, tmp_path, transport=Market(sofr_status=500))
    assert m.sofr_pct.value == 4.1 and m.sofr_pct.tag == "override"
