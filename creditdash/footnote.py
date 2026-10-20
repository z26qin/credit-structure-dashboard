"""Find the debt footnote and debt 8-K items, and turn their HTML into text an LLM can read.

Tables become pipe rows (`| DDTL 1.0 Facility | Mar 2028 | 15% | $1,300 |`) so rows and columns survive.
"""
from __future__ import annotations

import html as html_lib
import re
import warnings

from bs4 import BeautifulSoup, NavigableString, XMLParsedAsHTMLWarning

TEXT_BLOCKS = ("LongTermDebtTextBlock", "DebtDisclosureTextBlock", "DebtTextBlock")
BLOCKS = ["p", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section", "article"]
DEBT_HEADING = re.compile(r"^(note\s*)?\d*\.?\s*(debt|borrowings|long-term debt|notes payable|indebtedness|"
                          r"convertible (debt|notes|senior notes))\b", re.I)
NOTE_HEADING = re.compile(r"^(note\s*)?\d+\.\s+\w", re.I)
NOTE_NUMBER = re.compile(r"^(note\s*)?\d+\.\s*", re.I)
ITEM_HEADING = re.compile(r"^Item\s+(\d\.\d\d)\b", re.I)
MIN_SECTION_CHARS = 200  # shorter matches are table-of-contents entries
JOIN_RIGHT = {"$", "€", "£", "("}
JOIN_LEFT = {"%", ")", ")%", "%)"}


def _cells(row) -> list[str]:
    raw = [_squash(td.get_text("")) for td in row.find_all(["td", "th"])]
    cells: list[str] = []
    pending = ""
    for c in raw:
        if not c:
            continue
        if c in JOIN_RIGHT:
            pending += c
            continue
        if c in JOIN_LEFT and cells:
            cells[-1] += c
            continue
        cells.append(pending + c)
        pending = ""
    return cells


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\xa0", " ")).strip()


def html_to_text(html: str) -> str:
    with warnings.catch_warnings():  # inline-XBRL filings are XHTML; reading them as HTML is intended
        warnings.simplefilter("ignore", XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html, "lxml")
    for br in soup.find_all("br"):
        br.replace_with(" ")
    for table in soup.find_all("table"):
        rows = ["| " + " | ".join(cells) + " |" for tr in table.find_all("tr") if (cells := _cells(tr))]
        table.replace_with(NavigableString("\n" + "\n".join(rows) + "\n"))
    for el in soup.find_all(BLOCKS):
        el.insert_before("\n")
        el.insert_after("\n")
    lines = (_squash(line) for line in soup.get_text("").split("\n"))
    return "\n".join(line for line in lines if line)


def debt_note_from_instance(instance_xml: str) -> str | None:
    for tag in TEXT_BLOCKS:
        m = re.search(rf"<us-gaap:{tag}\b[^>]*>(.*?)</us-gaap:{tag}>", instance_xml, re.S)
        if m:
            body = m.group(1).strip()
            if body.startswith("<![CDATA["):
                body = body[9:].removesuffix("]]>")
            text = html_to_text(html_lib.unescape(body))
            if text:
                return text
    return None


def _plain(line: str) -> str:
    """A line as a heading reads: table pipes and zero-width spaces removed ("| 12. | CONVERTIBLE DEBT |")."""
    return re.sub(r"\s+", " ", line.replace("|", " ").replace("\u200b", " ")).strip()


def _is_heading(line: str, pattern: re.Pattern) -> bool:
    text = _plain(line)
    # a heading has no figures after its note number, unlike a balance-sheet row such as "Debt, current 24.5"
    return len(text) <= 80 and bool(pattern.match(text)) and not re.search(r"\d", NOTE_NUMBER.sub("", text))


def debt_note_from_html(filing_html: str) -> str | None:
    lines = html_to_text(filing_html).splitlines()
    sections: list[list[str]] = []
    for i, line in enumerate(lines):
        if _is_heading(line, DEBT_HEADING):
            end = next((j for j in range(i + 1, len(lines)) if _is_heading(lines[j], NOTE_HEADING)), len(lines))
            sections.append(lines[i:end])
    if not sections:
        return None
    full = next((s for s in sections if len("\n".join(s)) >= MIN_SECTION_CHARS), None)
    return "\n".join(full or max(sections, key=lambda s: len("\n".join(s))))


def eight_k_items(html: str, items: tuple[str, ...] = ("1.01", "2.03")) -> str:
    out: list[list[str]] = []
    current: list[str] | None = None
    for line in html_to_text(html).splitlines():
        m = ITEM_HEADING.match(line) if len(line) <= 200 else None
        if m:
            current = [line] if m.group(1) in items else None
            if current is not None:
                out.append(current)
        elif current is not None:
            current.append(line)
    return "\n\n".join("\n".join(section) for section in out)
