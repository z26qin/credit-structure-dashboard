"""Local web page: a ticker box that runs a build in the background, and the built dashboards."""
from __future__ import annotations

import re
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape

from . import pipeline
from .config import Settings
from .errors import CreditdashError

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

    def run(job_id: str, ticker: str) -> None:
        job = jobs[job_id]
        try:
            path = build_fn(ticker, settings=settings, progress=lambda stage: job.update(stage=stage))
            job.update(status="done", path=f"/d/{path.parent.parent.name}/{path.parent.name}")
        except CreditdashError as e:
            job.update(status="error", error=str(e))
        except Exception as e:  # surface anything else to the page instead of a silent dead job
            job.update(status="error", error=f"internal error: {type(e).__name__}: {e}")
        finally:
            with lock:
                running.pop(ticker, None)

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _env.get_template("index.html.j2").render(dashboards=built())

    @app.post("/build")
    async def start(request: Request) -> JSONResponse:
        try:
            body = await request.json()
        except ValueError:
            body = {}
        ticker = str((body or {}).get("ticker", "")).strip().upper().replace(".", "-")
        if not TICKER.match(ticker):
            raise HTTPException(400, "ticker must be 1-10 letters, digits or dashes")
        with lock:
            if ticker in running:
                raise HTTPException(409, f"{ticker} is already building")
            job_id = uuid.uuid4().hex
            jobs[job_id] = {"status": "running", "ticker": ticker, "stage": "queued", "path": None, "error": None}
            running[ticker] = job_id
        threading.Thread(target=run, args=(job_id, ticker), daemon=True).start()
        return JSONResponse({"job": job_id})

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

    return app
