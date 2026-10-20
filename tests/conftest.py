from dataclasses import replace
from pathlib import Path

import httpx
import pytest

from creditdash import config
from creditdash.config import Settings

FIXTURES = Path(__file__).parent / "fixtures" / "crwv-2026q2"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/1769628"

SEC_FILES = {
    "https://www.sec.gov/files/company_tickers.json": "company_tickers.json",
    "https://data.sec.gov/submissions/CIK0001769628.json": "submissions.json",
    "https://data.sec.gov/api/xbrl/companyfacts/CIK0001769628.json": "companyfacts.json",
    f"{ARCHIVE}/000176962826000366/crwv-20260630_htm.xml": "instance.xml",
    f"{ARCHIVE}/000176962826000104/crwv-20251231_htm.xml": "instance-10k.xml",
    f"{ARCHIVE}/000176962826000357/crwv-20260807.htm": "8k-20260807.htm",
    f"{ARCHIVE}/000176962826000432/crwv-20260917.htm": "8k-20260917.htm",
}


class FixtureTransport(httpx.MockTransport):
    """Serves fixture files by URL (404 otherwise) and records every URL requested."""

    def __init__(self, files: dict[str, str | bytes] | None = None):
        self.calls: list[str] = []
        self.files: dict[str, str | bytes] = dict(SEC_FILES) | (files or {})
        super().__init__(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append(url)
        body = self.files.get(url)
        if body is None:
            return httpx.Response(404)
        if isinstance(body, str):  # a fixture file name
            body = (FIXTURES / body).read_bytes()
        return httpx.Response(200, content=body)


@pytest.fixture(autouse=True)
def no_dotenv(monkeypatch, tmp_path):
    """Tests never read the developer's .env."""
    monkeypatch.setattr(config, "ENV_FILE", tmp_path / "no.env")


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(sec_user_agent="Test test@example.com", sec_max_rps=1000.0, home=tmp_path / ".creditdash",
                    out=tmp_path / "out", llm_api_key="k", llm_base_url="https://api.deepseek.com",
                    llm_model="deepseek-flash", llm_max_input_tokens=100_000)


@pytest.fixture
def sec_transport() -> FixtureTransport:
    return FixtureTransport()


@pytest.fixture
def edgar(settings, sec_transport):
    from creditdash.edgar import Edgar
    return Edgar(settings, transport=sec_transport)


def with_settings(s: Settings, **kw) -> Settings:
    return replace(s, **kw)
