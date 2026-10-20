import httpx
import pytest

from creditdash import edgar as edgar_mod
from creditdash.edgar import Edgar, archive_url, instance_doc
from creditdash.errors import SecError

URL = "https://data.sec.gov/x.json"


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(edgar_mod.time, "sleep", lambda s: None)


def make(settings, handler) -> Edgar:
    return Edgar(settings, transport=httpx.MockTransport(handler))


def test_sends_user_agent(settings):
    seen = []
    e = make(settings, lambda r: seen.append(r.headers["User-Agent"]) or httpx.Response(200, json={"ok": 1}))
    assert e.json(URL, None, None) == {"ok": 1}
    assert seen == ["Test test@example.com"]


def test_retries_429_then_ok(settings):
    replies = iter([httpx.Response(429), httpx.Response(200, json={"ok": 1})])
    e = make(settings, lambda r: next(replies))
    assert e.json(URL, None, None) == {"ok": 1}
    assert e.requests == 2


def test_403_names_user_agent(settings):
    e = make(settings, lambda r: httpx.Response(403))
    with pytest.raises(SecError, match="SEC_USER_AGENT"):
        e.json(URL, None, None)


def test_404_is_sec_error(settings):
    e = make(settings, lambda r: httpx.Response(404))
    with pytest.raises(SecError, match="404"):
        e.text(URL, None, None)


def test_cache_hit_makes_no_request(settings, tmp_path):
    e = make(settings, lambda r: httpx.Response(200, json={"ok": 1}))
    cache = tmp_path / "c" / "x.json"
    e.json(URL, cache, None)
    assert e.json(URL, cache, None) == {"ok": 1}
    assert e.requests == 1


def test_max_age_zero_refetches(settings, tmp_path):
    e = make(settings, lambda r: httpx.Response(200, json={"ok": 1}))
    cache = tmp_path / "x.json"
    e.json(URL, cache, 0)
    e.json(URL, cache, 0)
    assert e.requests == 2


def test_instance_doc():
    assert instance_doc("crwv-20260630.htm") == "crwv-20260630_htm.xml"


def test_archive_url():
    assert archive_url(1769628, "0001769628-26-000366", "x.htm") == \
        "https://www.sec.gov/Archives/edgar/data/1769628/000176962826000366/x.htm"


def test_serves_fixture(edgar, sec_transport):
    assert edgar.json("https://data.sec.gov/submissions/CIK0001769628.json", None, None)["cik"] == "0001769628"
    assert sec_transport.calls == ["https://data.sec.gov/submissions/CIK0001769628.json"]
