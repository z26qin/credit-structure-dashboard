"""README.md -> docs/index.html, the GitHub Pages copy of the README in the page style of docs/page.html.

    uv run python scripts/build_pages.py
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    readme = (ROOT / "README.md").read_text()
    page = (ROOT / "docs" / "page.html").read_text()
    # The README sits inside a <script> block: a literal "</script" would end it early.
    html = page.replace("{{README}}", readme.replace("</script", "<\\/script"))
    (ROOT / "docs" / "index.html").write_text(html)
    (ROOT / "docs" / ".nojekyll").touch()  # serve files as they are; no Jekyll build of docs/
    print(ROOT / "docs" / "index.html")


if __name__ == "__main__":
    main()
