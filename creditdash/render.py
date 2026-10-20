"""Dashboard -> one self-contained HTML file (data, d3, styles and script inlined; opens offline)."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from .model import MATURED, NO_RATE
from .models import Dashboard

HERE = Path(__file__).parent
SECTION_IDS = ["kpis", "stack", "yield", "paying", "wall", "coverage", "raise", "waterfall", "instruments", "method"]

_env = Environment(loader=FileSystemLoader(HERE / "templates"), autoescape=select_autoescape(["html", "j2"]))


def _json_for_script(dashboard: Dashboard) -> Markup:
    """JSON that cannot close its <script> tag: <, > and & become \\u escapes, still valid JSON."""
    data = dashboard.model_dump_json()
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
        js=Markup((static / "dashboard.js").read_text()),
    )


def write_outputs(dashboard: Dashboard, out_dir: Path) -> Path:
    folder = out_dir / dashboard.company.ticker / dashboard.financials.period_end.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "dashboard.json").write_text(dashboard.model_dump_json(indent=1))
    path = folder / "index.html"
    path.write_text(render(dashboard))
    return path
