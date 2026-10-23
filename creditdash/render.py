"""Dashboard, or a two-company Comparison -> one self-contained HTML file (data, d3, styles and script inlined; opens
offline)."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from .model import MATURED, NO_RATE
from .models import Comparison, Dashboard

HERE = Path(__file__).parent
SECTION_IDS = ["kpis", "stack", "yield", "paying", "wall", "coverage", "raise", "waterfall", "instruments", "method"]
COMPARE_SECTION_IDS = ["verdict", "tests", "coverage-path", "adequacy", "quarters", "runway", "maturity", "measures",
                       "method"]
COMPARE_DIR = "_compare"  # out/_compare/<A>_vs_<B>/: neither the underscore nor "_vs_" can be part of a ticker

_env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html", "j2"]))


def _json_for_script(model: Dashboard | Comparison) -> Markup:
    """JSON that cannot close its <script> tag: <, > and & become \\u escapes, still valid JSON."""
    data = model.model_dump_json()
    return Markup(data.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026"))


def render(dashboard: Dashboard) -> str:
    static = HERE / "static"
    excluded = dashboard.model.excluded
    return _env.get_template("dashboard.html.j2").render(
        d=dashboard,
        no_rate=[e.removesuffix(" " + NO_RATE) for e in excluded if e.endswith(NO_RATE)],
        matured=[e.removesuffix(" " + MATURED) for e in excluded if e.endswith(MATURED)],
        data_json=_json_for_script(dashboard),
        css=Markup((static / "dashboard.css").read_text()),
        d3=Markup((static / "d3.v7.min.js").read_text()),
        charts=Markup((static / "charts.js").read_text()),
        js=Markup((static / "dashboard.js").read_text()),
    )


def render_comparison(comparison: Comparison) -> str:
    static = HERE / "static"
    return _env.get_template("compare.html.j2").render(
        c=comparison,
        data_json=_json_for_script(comparison),
        css=Markup((static / "dashboard.css").read_text() + (static / "compare.css").read_text()),
        d3=Markup((static / "d3.v7.min.js").read_text()),
        charts=Markup((static / "charts.js").read_text()),
        js=Markup((static / "compare.js").read_text()),
    )


def write_outputs(dashboard: Dashboard, out_dir: Path) -> Path:
    folder = out_dir / dashboard.company.ticker / dashboard.financials.period_end.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "dashboard.json").write_text(dashboard.model_dump_json(indent=1))
    path = folder / "index.html"
    path.write_text(render(dashboard))
    return path


def comparison_folder(out_dir: Path, ticker_a: str, ticker_b: str) -> Path:
    return out_dir / COMPARE_DIR / f"{ticker_a}_vs_{ticker_b}"


def write_comparison(comparison: Comparison, out_dir: Path) -> Path:
    folder = comparison_folder(out_dir, comparison.a.ticker, comparison.b.ticker)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "comparison.json").write_text(comparison.model_dump_json(indent=1))
    path = folder / "index.html"
    path.write_text(render_comparison(comparison))
    return path
