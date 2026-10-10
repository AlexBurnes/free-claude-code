"""Generate opt-in profile convenience launchers in an explicit directory."""

import argparse
from pathlib import Path

from free_claude_code.config.loader import get_settings
from free_claude_code.config.profiles import ProfileStore

from .launchers.profiles import generate_profile_launchers


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    launchers = commands.add_parser("launchers")
    launchers.add_argument("--output-dir", type=Path, required=True)
    launchers.add_argument("--harness", default="claude")
    launchers.add_argument("names", nargs="*")
    args = parser.parse_args()
    names = args.names or [
        name for name in ProfileStore().list(get_settings()) if name != "default"
    ]
    try:
        paths = generate_profile_launchers(args.output_dir, names, args.harness)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    for path in paths:
        print(path)
