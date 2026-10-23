"""creditdash build <TICKER> [--refresh] [--no-llm] | compare <A> <B> [...] | serve [--port 8765] | mcp | list"""
from __future__ import annotations

import argparse
import sys

from . import pipeline
from .config import Settings
from .errors import CreditdashError
from .render import COMPARE_DIR


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="creditdash", description="Ticker in, credit-structure dashboard out.")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("build", help="build the dashboard for a ticker")
    b.add_argument("ticker")
    b.add_argument("--refresh", action="store_true", help="ignore cached SEC files and cached LLM answers")
    b.add_argument("--no-llm", action="store_true",
                   help="no model calls: SEC totals only, no 6-K figures, no market read")
    c = sub.add_parser("compare", help="build two tickers and compare repayment capacity and cash flow")
    c.add_argument("ticker_a")
    c.add_argument("ticker_b")
    c.add_argument("--refresh", action="store_true", help="ignore cached SEC files and cached LLM answers")
    c.add_argument("--no-llm", action="store_true", help="no model calls: SEC totals only")
    s = sub.add_parser("serve", help="run the local web page")
    s.add_argument("--port", type=int, default=8765)
    sub.add_parser("mcp", help="run as an MCP server over stdio (Claude Desktop, Claude Code)")
    sub.add_parser("list", help="list built dashboards")
    args = parser.parse_args(argv)
    if args.command == "mcp":  # before Settings: CREDITDASH_ROOT decides which .env is read
        from .mcp_server import main as mcp_main
        mcp_main()
        return 0

    settings = Settings.from_env()
    try:
        if args.command == "build":
            path = pipeline.build(args.ticker, settings=settings, refresh=args.refresh, no_llm=args.no_llm,
                                  progress=lambda stage: print(f"  {stage}…", file=sys.stderr))
            print(path)
        elif args.command == "compare":
            path = pipeline.build_comparison(args.ticker_a, args.ticker_b, settings=settings, refresh=args.refresh,
                                             no_llm=args.no_llm,
                                             progress=lambda stage: print(f"  {stage}…", file=sys.stderr))
            print(path)
        elif args.command == "serve":
            import uvicorn

            from .web import create_app
            uvicorn.run(create_app(settings), host="127.0.0.1", port=args.port)
        elif args.command == "list":
            for path in sorted(settings.out.glob("*/*/index.html")):
                if path.parent.parent.name != COMPARE_DIR:
                    print(f"{path.parent.parent.name:8} {path.parent.name}  {path}")
            for path in sorted((settings.out / COMPARE_DIR).glob("*/index.html")):
                print(f"{'compare':8} {path.parent.name.replace('_vs_', ' vs ')}  {path}")
    except CreditdashError as e:
        print(f"error: {e}", file=sys.stderr)
        return e.exit_code
    return 0


if __name__ == "__main__":
    sys.exit(main())
