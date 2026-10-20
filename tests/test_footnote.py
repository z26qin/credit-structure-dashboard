import pytest

from creditdash.footnote import debt_note_from_html, debt_note_from_instance, eight_k_items, html_to_text
from tests.conftest import FIXTURES


@pytest.fixture(scope="module")
def instance_xml():
    return (FIXTURES / "instance.xml").read_text()


def test_crwv_note_from_instance(instance_xml):
    t = debt_note_from_instance(instance_xml)
    assert 20_000 < len(t) < 40_000 and "DDTL 4.0 Facility" in t
    assert any(line.startswith("|") and "DDTL 1.0 Facility" in line and "1,300" in line for line in t.splitlines())


def test_no_text_block():
    assert debt_note_from_instance("<xbrl></xbrl>") is None


def test_heading_fallback_borrowings():
    html = ("<p><b>Note 8. Leases</b></p><p>x</p><p><b>Note 9. Borrowings and Credit Facilities</b></p>"
            "<p>Term loan due 2029</p><p><b>Note 10. Equity</b></p>")
    assert debt_note_from_html(html).splitlines() == ["Note 9. Borrowings and Credit Facilities", "Term loan due 2029"]


def test_heading_fallback_skips_table_of_contents():
    body = "<p>The Term Loan bears interest at SOFR plus 2.00% and matures in 2029. </p>" * 10
    html = ("<p>Note 7. Debt</p><p>Note 8. Equity</p>"  # table of contents
            f"<p><b>Note 7. Debt</b></p>{body}<p><b>Note 8. Equity</b></p>")
    t = debt_note_from_html(html)
    assert t.startswith("Note 7. Debt") and "Term Loan" in t and "Equity" not in t


def test_heading_fallback_none():
    assert debt_note_from_html("<p>nothing</p>") is None


def test_table_cells_merge_currency_and_drop_empty():
    html = "<table><tr><td></td><td>DDTL 1.0</td><td>$</td><td>1,300</td><td></td><td>15</td><td>%</td></tr></table>"
    assert html_to_text(html) == "| DDTL 1.0 | $1,300 | 15% |"


def test_inline_spans_join_without_spaces():
    assert html_to_text("<div><span>I</span><span>tem 7.01</span></div>") == "Item 7.01"


def test_eight_k_items():
    t = eight_k_items((FIXTURES / "8k-20260807.htm").read_text())
    assert t.startswith("Item 1.01") and "Item 2.03" in t and "Item 7.01" not in t


def test_eight_k_items_only_wanted():
    t = eight_k_items((FIXTURES / "8k-20260917.htm").read_text())
    assert t.startswith("Item 1.01") and "Item 3.02" not in t and "Item 8.01" not in t
