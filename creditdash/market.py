"""Market inputs: SOFR (NY Fed), share price (Yahoo, unofficial) and the optional overrides/<TICKER>.yaml.

Every source may fail; a failed value is null with the reason in its note, and the build carries on.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import yaml

from .errors import ConfigError
from .models import ConsensusYear, Fact, Market

SOFR_URL = "https://markets.newyorkfed.org/api/rates/secured/sofr/last/1.json"
BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko)"


def price_url(ticker: str) -> str:
    return f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=5d&interval=1d"


def load_overrides(ticker: str, overrides_dir: Path) -> dict:
    path = overrides_dir / f"{ticker}.yaml"
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text()) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: {e}") from e


def fetch_market(ticker: str, shares: Fact, overrides_dir: Path,
                 transport: httpx.BaseTransport | None = None) -> Market:
    o = load_overrides(ticker, overrides_dir)
    with httpx.Client(transport=transport, timeout=20, follow_redirects=True) as client:
        sofr = _sofr(client)
        if o.get("price") is not None:
            price = Fact(value=float(o["price"]), tag="override", note=f"overrides/{ticker}.yaml")
        else:
            price = _price(client, ticker)
    if price.value is not None and shares.value:
        cap = Fact(value=price.value * shares.value / 1e6, tag="price × shares outstanding", period=price.period)
    else:
        cap = Fact(value=None, note="needs a share price and shares outstanding")
    consensus = [ConsensusYear(fiscal_year=int(fy), **(v or {})) for fy, v in (o.get("consensus") or {}).items()]
    return Market(sofr_pct=sofr, price=price, market_cap=cap, consensus=sorted(consensus, key=lambda c: c.fiscal_year),
                  ratings={str(k): str(v) for k, v in (o.get("ratings") or {}).items()},
                  bond_yields={str(k): float(v) for k, v in (o.get("bond_yields") or {}).items()},
                  **({"ebitda_label": str(o["ebitda_label"])} if o.get("ebitda_label") else {}))


def _sofr(client: httpx.Client) -> Fact:
    try:
        r = client.get(SOFR_URL)
        r.raise_for_status()
        row = r.json()["refRates"][0]
        return Fact(value=float(row["percentRate"]), tag="NY Fed SOFR", period=row["effectiveDate"])
    except Exception as e:
        return Fact(value=None, note=f"NY Fed SOFR: {type(e).__name__}: {e}"[:300])


def _price(client: httpx.Client, ticker: str) -> Fact:
    try:
        r = client.get(price_url(ticker), headers={"User-Agent": BROWSER_UA})
        r.raise_for_status()
        meta = r.json()["chart"]["result"][0]["meta"]
        when = datetime.fromtimestamp(meta["regularMarketTime"], tz=timezone.utc).date().isoformat()
        return Fact(value=float(meta["regularMarketPrice"]), tag="Yahoo Finance regularMarketPrice", period=when,
                    note="unofficial endpoint")
    except Exception as e:
        return Fact(value=None, note=f"Yahoo price: {type(e).__name__}: {e}"[:300])
