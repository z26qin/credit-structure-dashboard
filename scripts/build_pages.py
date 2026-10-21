"""README.md -> docs/index.html, the GitHub Pages copy of the README in the page style of docs/page.html.

    uv run python scripts/build_pages.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PAGES_URL = "https://z26qin.github.io/credit-structure-dashboard/"


def main() -> None:
    # The README's pointer to this page is left out of the page itself.
    readme = "".join(line for line in (ROOT / "README.md").read_text().splitlines(keepends=True)
                     if PAGES_URL not in line)
    page = (ROOT / "docs" / "page.html").read_text()
    # The README sits inside a <script> block: a literal "</script" would end it early.
    html = page.replace("{{README}}", readme.replace("</script", "<\\/script"))
    (ROOT / "docs" / "index.html").write_text(html)
    (ROOT / "docs" / ".nojekyll").touch()  # serve files as they are; no Jekyll build of docs/
    print(ROOT / "docs" / "index.html")


if __name__ == "__main__":
    main()
