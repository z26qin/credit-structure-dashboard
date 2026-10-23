"""`creditdash mcp`: the pipeline as an MCP server over stdio, for Claude Desktop (.mcpb) and Claude Code.

Tools: credit_snapshot (build, then a compact summary), show_dashboard (the page, in the chat where the client
renders MCP Apps, else in the browser), compare_credit and show_comparison (two tickers side by side),
set_market_inputs (overrides/<TICKER>.yaml) and list_dashboards. dashboard_html and comparison_html are for the
viewer only: they hand the built page to the iframe.

Relative paths (.env, overrides/, config/, out/, .creditdash/) resolve against CREDITDASH_ROOT when it is set.
"""
from __future__ import annotations

import json
import os
import threading
import webbrowser
from pathlib import Path
from typing import Any, Callable

import anyio
import anyio.from_thread
import anyio.to_thread
import yaml
from mcp.server.apps import Apps, client_supports_apps
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult, TextContent, ToolAnnotations
from pydantic import BaseModel, Field

from . import pipeline
from .config import Settings
from .errors import ConfigError, CreditdashError
from .market import load_overrides
from .compare import summary
from .models import Dashboard
from .render import comparison_folder
from .snapshot import snapshot
from .web import PERIOD, TICKER

VIEW_URI = "ui://creditdash/dashboard.html"
OVERRIDES = Path("overrides")

INSTRUCTIONS = """creditdash reads a company's latest SEC filings (10-K/10-Q, or a US GAAP 20-F with its 6-Ks) and builds
a credit-structure dashboard: who it owes, in what order, when it comes due, and whether cash flow covers it.
Money is in $ millions. Start with credit_snapshot(ticker) and answer from its fields; call show_dashboard when
the user wants to see the page. A first build of a filing takes 30-80 s; rebuilds come from cache in seconds.
To weigh two companies against each other (repayment capacity, cash-flow adequacy, how tight the cash is), call
compare_credit(ticker_a, ticker_b), and show_comparison for the page.
Bond yields, 5-year CDS, ratings, consensus and price are not in the filings: when the user gives them, save
them with set_market_inputs and call credit_snapshot again. Each instrument's "verified" says whether its quotes
were found in the filing text; "reconciliation" says whether the instruments add up to the reported debt."""

# The viewer: a shell that, once the host hands it the show_dashboard result, asks dashboard_html for the
# built page and writes it into itself. The page is self-contained (data, d3, styles inlined), so the
# default MCP Apps CSP (inline script and style, no network) is enough.
VIEW_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>creditdash</title>
<style>body{font:14px/1.5 system-ui,sans-serif;margin:16px;color:#333}</style></head>
<body><p id="status">Waiting for the dashboard…</p>
<script>
(() => {
  let next = 0;
  const pending = new Map();
  const status = (t) => { document.getElementById("status").textContent = t; };
  const request = (method, params) => new Promise((resolve, reject) => {
    const id = ++next;
    pending.set(id, { resolve, reject });
    window.parent.postMessage({ jsonrpc: "2.0", id, method, params }, "*");
  });
  const notify = (method, params) => window.parent.postMessage({ jsonrpc: "2.0", method, params }, "*");
  const size = () => notify("ui/notifications/size-changed",
    { width: document.documentElement.scrollWidth, height: document.documentElement.scrollHeight });

  async function show(result) {
    const s = (result && result.structuredContent) || {};
    if (result && result.isError) { status(((result.content || [])[0] || {}).text || "The build failed."); size(); return; }
    const pair = s.ticker_a && s.ticker_b;
    if (!pair && (!s.ticker || !s.period)) { status("No dashboard in this result."); size(); return; }
    status(pair ? `Loading ${s.ticker_a} vs ${s.ticker_b}…` : `Loading ${s.ticker} ${s.period}…`);
    try {
      const r = await request("tools/call", pair
        ? { name: "comparison_html", arguments: { ticker_a: s.ticker_a, ticker_b: s.ticker_b } }
        : { name: "dashboard_html", arguments: { ticker: s.ticker, period: s.period } });
      const html = ((r && r.content) || [])[0];
      if (!html || r.isError) throw new Error((html && html.text) || "no page returned");
      document.open();
      document.write(html.text + SIZE_SCRIPT);
      document.close();
    } catch (e) {
      status(`Could not load the page (${e.message || e}). It is saved at ${s.path}.`);
      size();
    }
  }

  window.addEventListener("message", (e) => {
    const m = e.data;
    if (!m || m.jsonrpc !== "2.0") return;
    if (m.id !== undefined && !m.method && pending.has(m.id)) {
      const p = pending.get(m.id);
      pending.delete(m.id);
      m.error ? p.reject(new Error(m.error.message)) : p.resolve(m.result);
    } else if (m.method === "ui/notifications/tool-result") {
      show(m.params);
    }
  });

  request("ui/initialize", { protocolVersion: "2026-01-26", appInfo: { name: "creditdash", version: "0.1.0" },
                             appCapabilities: {} })
    .then(() => { notify("ui/notifications/initialized", {}); size(); })
    .catch((e) => status(`The host did not answer (${e.message || e}).`));
})();
</script></body></html>
"""

# Appended to the written page: it no longer has the shell's script, so it reports its own height.
SIZE_SCRIPT = ("<script>(()=>{const post=()=>window.parent.postMessage({jsonrpc:'2.0',"
               "method:'ui/notifications/size-changed',params:{width:document.documentElement.scrollWidth,"
               "height:document.documentElement.scrollHeight}},'*');new ResizeObserver(post)"
               ".observe(document.documentElement);post();})()</script>")


class Consensus(BaseModel):
    revenue: float | None = None
    ebitda: float | None = None
    capex: float | None = None
    cfo: float | None = Field(default=None, description="operating cash flow")


def js_string(s: str) -> str:
    """A JavaScript string literal that is safe inside a <script> element."""
    return json.dumps(s).replace("</", "<\\/")


def normalize_ticker(ticker: str) -> str:
    t = ticker.strip().upper().replace(".", "-")
    if not TICKER.match(t):
        raise ToolError(f"{ticker!r} is not a ticker: use 1-10 letters, digits or dashes, e.g. CRWV or BRK-B.")
    return t


def built(out: Path, ticker: str | None = None) -> list[Path]:
    """index.html of each built dashboard, oldest period first."""
    paths = sorted(out.glob(f"{ticker or '*'}/*/index.html"))
    return [p for p in paths if TICKER.match(p.parent.parent.name) and PERIOD.match(p.parent.name)]


def load_dashboard(index_html: Path) -> Dashboard:
    return Dashboard.model_validate_json((index_html.parent / "dashboard.json").read_text())


def create_server(settings_fn: Callable[[], Settings] = Settings.from_env, build_fn=pipeline.build,
                  open_browser: Callable[[str], object] = webbrowser.open) -> MCPServer:
    apps = Apps()
    locks: dict[str, threading.Lock] = {}
    locks_guard = threading.Lock()

    def settings() -> Settings:
        try:
            s = settings_fn()
            s.user_agent()
            return s
        except ConfigError as e:
            raise ToolError(f"{e} In Claude Desktop, set it in Settings → Extensions → creditdash.") from e

    async def build(ticker: str, ctx: Context, refresh: bool = False, no_llm: bool = False) -> Path:
        s = settings()
        if not no_llm and not s.llm_api_key:
            no_llm = True  # without a key the pipeline would fail at extract: build the SEC totals instead
        total = len(pipeline.STAGES)

        def progress(stage: str) -> None:
            step = pipeline.STAGES.index(stage) if stage in pipeline.STAGES else 0
            anyio.from_thread.run(ctx.report_progress, step, total, f"{ticker}: {stage}")

        def run() -> Path:
            with locks_guard:
                lock = locks.setdefault(ticker, threading.Lock())
            with lock:  # a second call for the same ticker waits, then reads the cache
                return build_fn(ticker, settings=s, refresh=refresh, no_llm=no_llm, progress=progress,
                                overrides_dir=OVERRIDES)

        try:
            path = await anyio.to_thread.run_sync(run)
        except CreditdashError as e:
            raise ToolError(str(e)) from e
        await ctx.report_progress(total, total, f"{ticker}: done")
        return path

    async def newest_or_build(ticker: str, ctx: Context) -> Path:
        paths = built(settings_fn().out, ticker)
        return paths[-1] if paths else await build(ticker, ctx)

    def present(path: Path, ctx: Context, open_in_browser: bool | None) -> tuple[str, bool, bool]:
        """Where the page went: (file URL, shown in the chat, opened in the browser)."""
        in_chat = client_supports_apps(ctx)
        url = path.resolve().as_uri()
        opened = (open_in_browser if open_in_browser is not None else not in_chat) and bool(open_browser(url))
        return url, in_chat, opened

    @apps.tool(resource_uri=VIEW_URI, title="Show dashboard",
               annotations=ToolAnnotations(read_only_hint=False, idempotent_hint=True, open_world_hint=True))
    async def show_dashboard(ticker: str, ctx: Context, period: str | None = None,
                             open_in_browser: bool | None = None) -> CallToolResult:
        """Show the full interactive dashboard page (charts of the capital stack, maturity wall, coverage,
        cash to raise and the cash waterfall). Uses the newest build, or builds one if there is none.

        Args:
            ticker: e.g. CRWV.
            period: a built period end (YYYY-MM-DD) from list_dashboards; default the newest.
            open_in_browser: also open the page in the default browser. Default: only when this client
                cannot show it in the chat.
        """
        t = normalize_ticker(ticker)
        s = settings_fn()
        if period is not None:
            if not PERIOD.match(period) or not (s.out / t / period / "index.html").is_file():
                have = ", ".join(p.parent.name for p in built(s.out, t)) or "none"
                raise ToolError(f"No {t} dashboard for period {period!r}. Built periods: {have}.")
            path = s.out / t / period / "index.html"
        else:
            path = await newest_or_build(t, ctx)
        url, in_chat, opened = present(path, ctx, open_in_browser)
        d = load_dashboard(path)
        where = [w for w, on in (("shown in the chat", in_chat), ("opened in the browser", opened)) if on]
        text = (f"{d.company.name} ({t}) dashboard for the period ended {path.parent.name}, built "
                f"{d.built_at:%Y-%m-%d %H:%M}" + (f", {' and '.join(where)}" if where else "") + f". File: {url}")
        return CallToolResult(content=[TextContent(type="text", text=text)],
                              structured_content={"ticker": t, "period": path.parent.name, "path": str(path.resolve()),
                                                  "url": url, "shown_in_chat": in_chat, "opened_in_browser": opened})

    @apps.tool(resource_uri=VIEW_URI, visibility=["app"], structured_output=False,
               annotations=ToolAnnotations(read_only_hint=True))
    def dashboard_html(ticker: str, period: str, ctx: Context) -> str:
        """For the dashboard viewer only (returns the whole page's HTML). Use show_dashboard instead."""
        if not client_supports_apps(ctx):
            raise ToolError("dashboard_html is for the dashboard viewer. Use show_dashboard instead.")
        t = normalize_ticker(ticker)
        path = settings_fn().out / t / period / "index.html"
        if not PERIOD.match(period) or not path.is_file():
            raise ToolError(f"No {t} dashboard for period {period!r}.")
        return path.read_text()

    @apps.tool(resource_uri=VIEW_URI, title="Show comparison",
               annotations=ToolAnnotations(read_only_hint=False, idempotent_hint=True, open_world_hint=True))
    async def show_comparison(ticker_a: str, ticker_b: str, ctx: Context,
                              open_in_browser: bool | None = None) -> CallToolResult:
        """Show the page that compares two companies: the four coverage tests side by side, coverage year by year,
        operating cash flow against capex and principal, the last eight quarters, liquidity runway and the
        maturity profile. Uses each ticker's newest build, building the ones that are missing.

        Args:
            ticker_a: e.g. CRWV.
            ticker_b: e.g. NBIS.
            open_in_browser: also open the page in the default browser. Default: only when this client
                cannot show it in the chat.
        """
        a, b = normalize_ticker(ticker_a), normalize_ticker(ticker_b)
        index_a, index_b = await newest_or_build(a, ctx), await newest_or_build(b, ctx)
        path, c = pipeline.compare_builds(index_a, index_b, settings_fn().out)
        url, in_chat, opened = present(path, ctx, open_in_browser)
        where = [w for w, on in (("shown in the chat", in_chat), ("opened in the browser", opened)) if on]
        text = (f"{c.a.name} ({c.a.ticker}, period ended {c.a.period_end}) vs {c.b.name} ({c.b.ticker}, period ended "
                f"{c.b.period_end})" + (f", {' and '.join(where)}" if where else "") + f". File: {url}")
        return CallToolResult(content=[TextContent(type="text", text=text)],
                              structured_content={"ticker_a": c.a.ticker, "ticker_b": c.b.ticker,
                                                  "path": str(path.resolve()), "url": url, "shown_in_chat": in_chat,
                                                  "opened_in_browser": opened})

    @apps.tool(resource_uri=VIEW_URI, visibility=["app"], structured_output=False,
               annotations=ToolAnnotations(read_only_hint=True))
    def comparison_html(ticker_a: str, ticker_b: str, ctx: Context) -> str:
        """For the dashboard viewer only (returns the whole comparison page's HTML). Use show_comparison instead."""
        if not client_supports_apps(ctx):
            raise ToolError("comparison_html is for the dashboard viewer. Use show_comparison instead.")
        a, b = normalize_ticker(ticker_a), normalize_ticker(ticker_b)
        path = comparison_folder(settings_fn().out, a, b) / "index.html"
        if not path.is_file():
            raise ToolError(f"No {a} vs {b} comparison built. Use show_comparison.")
        return path.read_text()

    apps.add_html_resource(VIEW_URI, VIEW_HTML.replace("SIZE_SCRIPT", js_string(SIZE_SCRIPT)),
                           name="creditdash viewer", title="Credit-structure dashboard", prefers_border=True)
    server = MCPServer("creditdash", title="creditdash", instructions=INSTRUCTIONS, extensions=[apps])  # after apps.*

    @server.tool(title="Credit snapshot", structured_output=True,
                 annotations=ToolAnnotations(read_only_hint=False, idempotent_hint=True, open_world_hint=True))
    async def credit_snapshot(ticker: str, ctx: Context, refresh: bool = False, no_llm: bool = False) -> dict[str, Any]:
        """Build (or rebuild from cache) a company's credit-structure dashboard from its latest SEC filings and
        return a compact summary: can it pay its interest over the next 12 months (four coverage tests and a
        bottom line), balance sheet, trailing-12-month figures, every debt instrument with seniority, rate and
        maturity, the maturity schedule, the capital stack, market inputs and the market-implied default read.

        Args:
            ticker: US-listed ticker, e.g. CRWV. Class shares use a dash: BRK-B.
            refresh: re-download SEC files and re-ask the model instead of using the cache.
            no_llm: skip model calls (SEC totals only, faster, no instrument list).
        """
        t = normalize_ticker(ticker)
        path = await build(t, ctx, refresh=refresh, no_llm=no_llm)
        out = snapshot(load_dashboard(path))
        out["dashboard"] = {"ticker": t, "period": path.parent.name, "path": str(path.resolve())}
        if not no_llm and not settings().llm_api_key:
            out["notes"] = ["No LLM_API_KEY is set, so this is SEC totals only: no instrument list, no 6-K figures, "
                            "no market read. Add a DeepSeek key in the extension settings for the full build."]
        return out

    @server.tool(title="Compare two companies", structured_output=True,
                 annotations=ToolAnnotations(read_only_hint=False, idempotent_hint=True, open_world_hint=True))
    async def compare_credit(ticker_a: str, ticker_b: str, ctx: Context, refresh: bool = False,
                             no_llm: bool = False) -> dict[str, Any]:
        """Compare two companies' repayment capacity, cash-flow adequacy and cash-flow tightness. Builds both
        dashboards (from cache when it can) and returns, per company, the funding verdict, bottom line and the four
        coverage tests; then every measure for both with the stronger side named (coverage multiples, net debt ÷
        EBITDA, operating cash flow against capex and principal, free cash flow ÷ debt, liquidity runway with
        maturities paid or refinanced, principal due in 12 months, floating-rate share), a tally per section, and
        notes such as differing period ends. Ratios only, so size does not decide it.

        Args:
            ticker_a: US-listed ticker, e.g. CRWV.
            ticker_b: e.g. NBIS.
            refresh: re-download SEC files and re-ask the model instead of using the cache.
            no_llm: skip model calls (SEC totals only, no instrument list).
        """
        a, b = normalize_ticker(ticker_a), normalize_ticker(ticker_b)
        index_a = await build(a, ctx, refresh=refresh, no_llm=no_llm)
        index_b = await build(b, ctx, refresh=refresh, no_llm=no_llm)
        path, c = pipeline.compare_builds(index_a, index_b, settings_fn().out)
        out = summary(c)
        out["comparison"] = {"ticker_a": c.a.ticker, "ticker_b": c.b.ticker, "path": str(path.resolve())}
        if not no_llm and not settings().llm_api_key:
            out.setdefault("notes", []).append("No LLM_API_KEY is set, so both builds are SEC totals only: no "
                                               "instrument list, so no floating-rate share.")
        return out

    @server.tool(title="List dashboards", annotations=ToolAnnotations(read_only_hint=True))
    def list_dashboards() -> list[dict]:
        """List the dashboards built so far: ticker, company, period end, when it was built, file path."""
        rows = []
        for path in built(settings_fn().out):
            try:
                d = load_dashboard(path)
                name, at = d.company.name, d.built_at.isoformat()
            except (OSError, ValueError):
                name, at = None, None
            rows.append({"ticker": path.parent.parent.name, "company": name, "period": path.parent.name,
                         "built_at": at, "path": str(path.resolve())})
        return rows

    @server.tool(title="Set market inputs", structured_output=True,
                 annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True))
    def set_market_inputs(ticker: str, price: float | None = None, cds_5y_bps: float | None = None,
                          bond_yields: dict[str, float | None] | None = None,
                          ratings: dict[str, str | None] | None = None,
                          consensus: dict[int, Consensus | None] | None = None,
                          ebitda_label: str | None = None) -> dict[str, Any]:
        """Save market inputs the SEC filings don't have, in overrides/<TICKER>.yaml. Only the fields given
        change; inside bond_yields, ratings and consensus, an entry set to null is removed. Call credit_snapshot
        afterwards to rebuild with them (fast: filings and model answers come from cache).

        Args:
            ticker: e.g. CRWV.
            price: share price in dollars; when set, Yahoo is not asked.
            cds_5y_bps: 5-year CDS in basis points; drives the market-implied default probability.
            bond_yields: yield-to-worst % by bond name, e.g. {"2032 9.625% Senior Notes": 14.2}. Without a CDS,
                the highest yield less SOFR is used.
            ratings: rating by instrument name as the dashboard shows it, e.g. {"DDTL 4.0 Facility": "A3"}.
            consensus: per fiscal year (the calendar year it ends in), in $ millions, e.g.
                {2027: {"revenue": 26311, "ebitda": 16635, "capex": 47029, "cfo": 14613}}.
            ebitda_label: e.g. "adj. EBITDA" when consensus EBITDA is an adjusted figure.
        """
        t = normalize_ticker(ticker)
        try:
            current = load_overrides(t, OVERRIDES)
        except CreditdashError as e:
            raise ToolError(str(e)) from e
        for key, value in (("price", price), ("cds_5y_bps", cds_5y_bps), ("ebitda_label", ebitda_label)):
            if value is not None:
                current[key] = value
        for key, entries in (("bond_yields", bond_yields), ("ratings", ratings), ("consensus", consensus)):
            if entries is None:
                continue
            merged = dict(current.get(key) or {})
            for k, v in entries.items():
                if v is None:
                    merged.pop(k, None)
                else:
                    merged[k] = v.model_dump(exclude_none=True) if isinstance(v, Consensus) else v
            if merged:
                current[key] = merged
            else:
                current.pop(key, None)
        OVERRIDES.mkdir(parents=True, exist_ok=True)
        path = OVERRIDES / f"{t}.yaml"
        path.write_text(yaml.safe_dump(current, sort_keys=False, allow_unicode=True))
        return {"path": str(path.resolve()), "overrides": current,
                "next": f"Call credit_snapshot('{t}') to rebuild the dashboard with these inputs."}

    return server


def main() -> None:
    for key, value in list(os.environ.items()):  # an optional extension setting left blank may arrive unfilled
        if value.startswith("${user_config."):
            del os.environ[key]
    if root := os.environ.get("CREDITDASH_ROOT"):
        root_path = Path(root).expanduser()
        root_path.mkdir(parents=True, exist_ok=True)
        os.chdir(root_path)
    create_server().run("stdio")
