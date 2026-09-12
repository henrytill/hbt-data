"""``python3 -m hbt.conformance --binary path/to/hbt``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Sequence, TextIO

from hbt.conformance import __version__
from hbt.conformance.corpus import Corpus, revision
from hbt.conformance.runner import DEFAULT_TIMEOUT, Outcome, Result, check


def read_waivers(path: Path) -> list[str]:
    """Fixture names a caller expects to fail, one per line, ``#`` for comments."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [stripped for line in lines if (stripped := line.split("#", 1)[0].strip())]


def build_parser() -> argparse.ArgumentParser:
    """The command line."""
    parser = argparse.ArgumentParser(
        prog="hbt-conformance",
        description="Run the hbt corpus against an hbt executable.",
    )
    parser.add_argument("--binary", type=Path, required=True, help="the hbt executable to test")
    parser.add_argument("--corpus", type=Path, default=None, help="corpus root (defaults to this checkout)")
    parser.add_argument("--waivers", type=Path, default=None, help="file of fixture names expected to fail")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="per-fixture timeout in seconds")
    parser.add_argument("--tz", default=None, help="run under this timezone instead of the ambient one")
    parser.add_argument("-l", "--list", action="store_true", help="list the selected fixtures and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="report passing fixtures too")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("filter", nargs="*", help="fixture name, substring, or glob; all fixtures if omitted")
    return parser


def report(results: Sequence[Result], verbose: bool, out: TextIO) -> None:
    """Print each result worth printing, with its differences beneath it."""
    for result in results:
        if result.outcome is Outcome.PASS and not verbose:
            continue
        label = result.outcome.value.upper()
        suffix = f" -- {result.reason}" if result.reason else ""
        print(f"{label:>5}  {result.fixture.name}{suffix}", file=out)
        for difference in result.differences:
            for line in difference.render().splitlines():
                print(f"         {line}", file=out)


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    """Run the selected fixtures; 0 if every one of them conformed."""
    args = build_parser().parse_args(argv)
    stream: TextIO = out if out is not None else sys.stdout

    corpus = Corpus.discover(args.corpus)
    selected = corpus.select(args.filter)

    if args.list:
        for fixture in selected:
            print(fixture.name, file=stream)
        return 0

    if not selected:
        print(f"no fixture matches {' '.join(args.filter)}", file=stream)
        return 2

    waived: set[str] = set(read_waivers(args.waivers)) if args.waivers else set()

    print(
        f"corpus {revision(corpus.root)} • {len(selected)} fixture(s) • binary {args.binary}"
        + (f" • TZ={args.tz}" if args.tz else ""),
        file=stream,
    )

    results: list[Result] = []
    for fixture in selected:
        result = check(fixture, args.binary, args.timeout, args.tz)
        results.append(result.waive() if fixture.name in waived else result)

    report(results, args.verbose, stream)

    counts = {outcome: sum(1 for r in results if r.outcome is outcome) for outcome in Outcome}
    summary = ", ".join(f"{counts[o]} {o.value}" for o in Outcome if counts[o])
    print(summary, file=stream)

    stale = sorted(waived - {f.name for f in corpus.fixtures})
    for name in stale:
        print(f"warning: waiver for unknown fixture {name}", file=stream)

    return 0 if all(r.outcome.ok for r in results) and not stale else 1
