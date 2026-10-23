import threading
import time

import pytest
from fastapi.testclient import TestClient

from creditdash.errors import UnknownTicker
from creditdash.render import write_outputs
from creditdash.web import create_app
from tests.sample import sample_dashboard


def fake_build(gate: threading.Event | None = None):
    def build(ticker, *, settings, progress, **kw):
        for stage in ("resolve", "fetch", "render"):
            progress(stage)
        if gate is not None:
            gate.wait(5)
        if ticker == "ZZZZZ":
            raise UnknownTicker("Unknown ticker ZZZZZ")
        return write_outputs(sample_dashboard(ticker=ticker), settings.out)
    return build


def wait_done(client, job_id):
    for _ in range(200):
        job = client.get(f"/jobs/{job_id}").json()
        if job["status"] != "running":
            return job
        time.sleep(0.01)
    raise AssertionError("job did not finish")


@pytest.fixture
def client(settings):
    return TestClient(create_app(settings, build_fn=fake_build()))


def test_index_lists_dashboards(settings, client):
    write_outputs(sample_dashboard(ticker="CRWV"), settings.out)
    r = client.get("/")
    assert r.status_code == 200 and "/d/CRWV/2026-06-30" in r.text and 'id="ticker"' in r.text


def test_build_job_flow(client):
    r = client.post("/build", json={"ticker": "crwv"})
    assert r.status_code == 200
    job = wait_done(client, r.json()["job"])
    assert job["status"] == "done" and job["stage"] == "render" and job["path"] == "/d/CRWV/2026-06-30"
    assert client.get(job["path"]).status_code == 200


def test_build_error_reported(client):
    job = wait_done(client, client.post("/build", json={"ticker": "ZZZZZ"}).json()["job"])
    assert job["status"] == "error" and "ZZZZZ" in job["error"]


def test_bad_ticker_rejected(client):
    assert client.post("/build", json={"ticker": "../etc"}).status_code == 400
    assert client.post("/build", json={}).status_code == 400


def test_duplicate_build_409(settings):
    gate = threading.Event()
    client = TestClient(create_app(settings, build_fn=fake_build(gate)))
    first = client.post("/build", json={"ticker": "CRWV"})
    assert client.post("/build", json={"ticker": "CRWV"}).status_code == 409
    gate.set()
    assert wait_done(client, first.json()["job"])["status"] == "done"
    assert client.post("/build", json={"ticker": "CRWV"}).status_code == 200


def test_serves_dashboard(settings, client):
    write_outputs(sample_dashboard(ticker="CRWV"), settings.out)
    r = client.get("/d/CRWV/2026-06-30")
    assert r.status_code == 200 and "dashboard-data" in r.text


def test_unknown_job_404(client):
    assert client.get("/jobs/nope").status_code == 404


def test_rejects_traversal(settings, client):
    (settings.out).mkdir(parents=True, exist_ok=True)
    (settings.out.parent / "secret.txt").write_text("secret")
    for path in ["/d/..%2F..%2Fetc/passwd", "/d/CRWV/../../secret.txt", "/d/..%2F/secret.txt", "/d/CRWV/2026-06-31x"]:
        r = client.get(path)
        assert r.status_code == 404 and "secret" not in r.text, path


def test_compare_job_flow(client):
    r = client.post("/compare", json={"a": "crwv", "b": "nbis"})
    assert r.status_code == 200
    job = wait_done(client, r.json()["job"])
    assert job["status"] == "done" and job["path"] == "/c/CRWV/NBIS" and job["stage"] == "NBIS: render"
    page = client.get(job["path"])
    assert page.status_code == 200 and "comparison-data" in page.text and 'data-web="/d/CRWV/2026-06-30"' in page.text
    assert "/c/CRWV/NBIS" in client.get("/").text


def test_compare_errors(client):
    assert client.post("/compare", json={"a": "CRWV"}).status_code == 400
    job = wait_done(client, client.post("/compare", json={"a": "CRWV", "b": "ZZZZZ"}).json()["job"])
    assert job["status"] == "error" and "ZZZZZ" in job["error"]
    assert client.get("/c/CRWV/ZZZZZ").status_code == 404 and client.get("/c/..%2F/x").status_code == 404
