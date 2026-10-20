"""Golden check on the accepted DeepSeek extraction for CoreWeave's Q2 2026 10-Q (spec §1 criterion 1)."""
import json

import pytest

from creditdash.extract import build_inputs
from creditdash.facts import build_financials
from creditdash.models import Extraction, ReconStatus
from creditdash.reconcile import reconcile
from creditdash.resolve import resolve
from tests.conftest import FIXTURES

K1, K2 = "0001769628-26-000357", "0001769628-26-000432"


@pytest.fixture(scope="module")
def extraction():
    return Extraction.model_validate_json((FIXTURES / "extraction.json").read_text())


@pytest.fixture
def checked(edgar, extraction):
    r = resolve("CRWV", edgar)
    instance = (FIXTURES / "instance.xml").read_text()
    eight_ks = {K1: (FIXTURES / "8k-20260807.htm").read_text(), K2: (FIXTURES / "8k-20260917.htm").read_text()}
    annual = (FIXTURES / "instance-10k.xml").read_text()
    _, sources = build_inputs(r, instance, lambda: "", eight_ks, annual)
    fin = build_financials(json.loads((FIXTURES / "companyfacts.json").read_text()), instance, r.periodic.period_end,
                           "1231", r.periodic.accession)
    return reconcile(extraction, fin, sources)


def test_reconciles_within_1pct(checked):
    rec, instruments = checked
    assert rec.status == ReconStatus.pass_ and abs(rec.gap_pct) <= 0.01
    assert rec.unverified == []


def test_every_10q_facility_present(extraction):
    names = " | ".join(i.name for i in extraction.instruments)
    for facility in ["DDTL 1.0", "DDTL 2.0", "DDTL 2.1", "DDTL 3.0", "DDTL 4.0", "DDTL 5.0", "2030 Senior Notes",
                     "9.00%", "9.75%", "9.625%", "EUR", "2031 Convertible", "2032 Convertible", "Magnetar",
                     "Revolving Credit Facility"]:
        assert facility in names, facility
    assert sum(1 for i in extraction.instruments if "OEM" in i.name) == 2  # recourse and non-recourse rows


def test_post_period_items_recorded_once(extraction):
    ddtl55 = [i for i in extraction.instruments if "5.5" in i.name]
    assert len(ddtl55) == 1 and ddtl55[0].commitment == 2600 and ddtl55[0].source == K1
    assert not ddtl55[0].post_period_change  # the 8-K announces a commitment; it reports no draw
    converts = [i for i in extraction.instruments if "2.875%" in i.name]
    assert len(converts) == 1 and converts[0].post_period_change == 4200 and converts[0].source == K2
