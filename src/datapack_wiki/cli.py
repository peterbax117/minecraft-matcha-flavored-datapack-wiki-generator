from __future__ import annotations

import argparse
from pathlib import Path

from .builder import build
from .server import serve


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(prog="datapack-wiki")
    subcommands = command.add_subparsers(dest="command", required=True)

    build_command = subcommands.add_parser("build", help="Build a wiki from a pack ZIP.")
    build_command.add_argument("--pack", type=Path, required=True)
    build_command.add_argument("--output", type=Path, required=True)
    build_command.add_argument("--fetch-wiki-icons", action="store_true")
    build_command.add_argument("--reuse-site", type=Path)

    serve_command = subcommands.add_parser("serve", help="Serve a generated wiki.")
    serve_command.add_argument("--site", type=Path, required=True)
    serve_command.add_argument("--timeout-minutes", type=float, default=30)
    serve_command.add_argument("--no-browser", action="store_true")
    return command


def main() -> None:
    args = parser().parse_args()
    if args.command == "build":
        build(
            pack_path=args.pack,
            output=args.output,
            fetch_wiki_icons=args.fetch_wiki_icons,
            reuse_site=args.reuse_site,
        )
    else:
        serve(args.site, args.timeout_minutes, not args.no_browser)

