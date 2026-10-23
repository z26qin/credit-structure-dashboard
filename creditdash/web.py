"""Local web page: a ticker box that runs a build in the background, a pair of boxes that compares two tickers,
and the built pages."""
from __future__ import annotations

import re
import threading
import uuid
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import pipeline
from .config import Settings
from .errors import CreditdashError
from .render import COMPARE_DIR, comparison_folder

TICKER = re.compile(r"^[A-Z0-9-]{1,10}$")
PERIOD = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"), autoescape=select_autoescape(["j2"]))


def create_app(settings: Settings, build_fn=pipeline.build) -> FastAPI:
    app = FastAPI(title="creditdash", docs_url=None, redoc_url=None)
    jobs: dict[str, dict] = {}
    running: dict[str, str] = {}
    lock = threading.Lock()

    def built() -> list[dict]:
        out = []
        for path in sorted(settings.out.glob("*/*/index.html")):
            ticker, period = path.parent.parent.name, path.parent.name
            if TICKER.match(ticker) and PERIOD.match(period):
                out.append({"ticker": ticker, "period": period, "url": f"/d/{ticker}/{period}"})
        return out

    def comparisons() -> list[dict]:
        out = []
        for path in sorted((settings.out / COMPARE_DIR).glob("*_vs_*/index.html")):
            a, _, b = path.parent.name.partition("_vs_")
            if TICKER.match(a) and TICKER.match(b):
                out.append({"a": a, "b": b, "url": f"/c/{a}/{b}"})
        return out

    def run(job_id: str, key: str, work: Callable[[Callable[[str], None]], str]) -> None:
        job = jobs[job_id]
        try:
            job.update(status="done", path=work(lambda stage: job.update(stage=stage)))
        except CreditdashError as e:
            job.update(status="error", error=str(e))
        except Exception as e:  # surface anything else to the page instead of a silent dead job
            job.update(status="error", error=f"internal error: {type(e).__name__}: {e}")
        finally:
            with lock:
                running.pop(key, None)

    def start_job(key: str, label: str, work: Callable[[Callable[[str], None]], str]) -> JSONResponse:
        with lock:
            if key in running:
                raise HTTPException(409, f"{label} is already building")
            job_id = uuid.uuid4().hex
            jobs[job_id] = {"status": "running", "ticker": label, "stage": "queued", "path": None, "error": None}
            running[key] = job_id
        threading.Thread(target=run, args=(job_id, key, work), daemon=True).start()
        return JSONResponse({"job": job_id})

    def one(ticker: str) -> Callable[[Callable[[str], None]], str]:
        def work(progress: Callable[[str], None]) -> str:
            path = build_fn(ticker, settings=settings, progress=progress)
            return f"/d/{path.parent.parent.name}/{path.parent.name}"
        return work

    def pair(a: str, b: str) -> Callable[[Callable[[str], None]], str]:
        def work(progress: Callable[[str], None]) -> str:
            path = pipeline.build_comparison(a, b, settings=settings, progress=progress, build_fn=build_fn)
            ta, _, tb = path.parent.name.partition("_vs_")
            return f"/c/{ta}/{tb}"
        return work

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _env.get_template("index.html.j2").render(dashboards=built(), comparisons=comparisons())

    async def tickers(request: Request, *names: str) -> list[str]:
        try:
            body = await request.json()
        except ValueError:
            body = {}
        out = [str((body or {}).get(n, "")).strip().upper().replace(".", "-") for n in names]
        if not all(TICKER.match(t) for t in out):
            raise HTTPException(400, "ticker must be 1-10 letters, digits or dashes")
        return out

    @app.post("/build")
    async def start(request: Request) -> JSONResponse:
        (ticker,) = await tickers(request, "ticker")
        return start_job(ticker, ticker, one(ticker))

    @app.post("/compare")
    async def start_compare(request: Request) -> JSONResponse:
        a, b = await tickers(request, "a", "b")
        return start_job(f"{a}_vs_{b}", f"{a} vs {b}", pair(a, b))

    @app.get("/jobs/{job_id}")
    def job(job_id: str) -> dict:
        if job_id not in jobs:
            raise HTTPException(404, "no such job")
        return jobs[job_id]

    @app.get("/d/{ticker}/{period}", response_class=HTMLResponse)
    def dashboard(ticker: str, period: str) -> str:
        if not TICKER.match(ticker) or not PERIOD.match(period):
            raise HTTPException(404)
        path = settings.out / ticker / period / "index.html"
        if not path.is_file():
            raise HTTPException(404)
        return path.read_text()

    @app.get("/c/{a}/{b}", response_class=HTMLResponse)
    def comparison(a: str, b: str) -> str:
        if not TICKER.match(a) or not TICKER.match(b):
            raise HTTPException(404)
        path = comparison_folder(settings.out, a, b) / "index.html"
        if not path.is_file():
            raise HTTPException(404)
        return path.read_text()

    return app
