"""Package the MCP server as a Claude Desktop extension: dist/creditdash-<version>.mcpb.

    uv run python scripts/build_mcpb.py

Stages mcpb/manifest.json (version taken from pyproject.toml), a generated icon, pyproject.toml, uv.lock and
the creditdash package in dist/mcpb/, then runs `npx @anthropic-ai/mcpb validate` and `pack` (needs Node).
Claude Desktop installs the dependencies with uv on first start; nothing is vendored.
"""
import json
import shutil
import struct
import subprocess
import tomllib
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
STAGE = DIST / "mcpb"
MCPB = "@anthropic-ai/mcpb@2.1.2"


def icon_png(size: int = 512) -> bytes:
    """The capital-stack bar from the dashboard (secured to equity, colours from dashboard.css) on the page tone."""
    bg, bands = (0xf9, 0xf9, 0xf7), [(0x10, 0x42, 0x81), (0x25, 0x6a, 0xbf), (0x55, 0x98, 0xe7),
                                     (0x86, 0xb6, 0xef), (0xeb, 0x68, 0x34)]
    left, right, top, bottom, gap = (round(v * size / 256) for v in (72, 184, 28, 228, 6))
    step = (bottom - top + gap) // len(bands)
    rows = []
    for y in range(size):
        band = (y - top) // step if top <= y < bottom else -1
        inside = 0 <= band < len(bands) and (y - top) % step < step - gap
        colour = bands[band] if inside else bg
        rows.append(b"\x00" + b"".join(bytes(colour if inside and left <= x < right else bg) for x in range(size)))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(b"".join(rows), 9))
            + chunk(b"IEND", b""))


def main() -> None:
    version = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]
    shutil.rmtree(STAGE, ignore_errors=True)
    STAGE.mkdir(parents=True)
    manifest = json.loads((ROOT / "mcpb" / "manifest.json").read_text()) | {"version": version}
    (STAGE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (STAGE / "icon.png").write_bytes(icon_png())
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy2(ROOT / name, STAGE / name)
    shutil.copytree(ROOT / "creditdash", STAGE / "creditdash", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    out = DIST / f"creditdash-{version}.mcpb"
    subprocess.run(["npx", "-y", MCPB, "validate", str(STAGE / "manifest.json")], check=True)
    subprocess.run(["npx", "-y", MCPB, "pack", str(STAGE), str(out)], check=True)
    print(out)


if __name__ == "__main__":
    main()
