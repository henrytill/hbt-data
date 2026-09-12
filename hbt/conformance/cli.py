"""``python3 -m hbt.conformance --binary path/to/hbt``."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Sequence, TextIO

from hbt.conformance import __version__
from hbt.conformance.corpus import Corpus, Fixture, UnknownSidecar, revision
from hbt.conformance.runner import DEFAULT_TIMEOUT, Outcome, Result, check


def read_waivers(path: Path) -> dict[str, str]:
    """Fixture names a caller expects to fail, mapped to why.

    One per line, with the reason after ``#``.  The reason is kept rather than
    discarded: a waiver file is authored in an implementation's repository and
    read here, so a bare name would be a suppression with no recorded owner or
    exit condition -- and the concern it belongs to is what a conformance
    matrix has to key on.
    """
    waivers: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, _, reason = line.partition("#")
        if name.strip():
            waivers[name.strip()] = reason.strip() or "no reason recorded"
    return waivers


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
    parser.add_argument("-j", "--jobs", type=int, default=8, help="fixtures to run at once")
    parser.add_argument("-l", "--list", action="store_true", help="list the selected fixtures and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="report passing fixtures too")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("filter", nargs="*", help="fixture name, substring, or glob; all fixtures if omitted")
    return parser


def _header(corpus: Corpus, selected: int, binary: Path, tz: str | None) -> str:
    """The one line that says what is being checked, and against what.

    Coverage is stated rather than left implicit: only the HTML fixtures pin
    `-t html`, so "37 pass" alone would not say that the HTML formatter went
    unchecked on the other 28 inputs.  The corpus revision is here because a
    stale pin otherwise surfaces as dozens of opaque failures.
    """
    coverage = ", ".join(f"{n} {fmt}" for fmt, n in sorted(corpus.coverage().items()) if n)
    zone = f" \u2022 TZ={tz}" if tz else ""
    return f"corpus {revision(corpus.root)} \u2022 {selected} fixture(s) ({coverage}) \u2022 binary {binary}{zone}"


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


def _run(fixtures: list[Fixture], args: argparse.Namespace, waived: dict[str, str]) -> list[Result]:
    """Check every fixture, waiving the ones the caller expects to fail.

    Threads rather than a sequential loop: nearly all of the time is spent
    waiting on subprocesses.  `map` yields in submission order, so the report
    stays deterministic without any sorting.
    """
    binary: Path = args.binary
    timeout: float = args.timeout
    tz: str | None = args.tz

    def run_one(fixture: Fixture) -> Result:
        return check(fixture, binary, timeout, tz)

    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        return [r.waive(waived[r.fixture.name]) if r.fixture.name in waived else r for r in pool.map(run_one, fixtures)]


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    """Run the selected fixtures; 0 if every one of them conformed."""
    args = build_parser().parse_args(argv)
    stream: TextIO = out if out is not None else sys.stdout

    try:
        corpus = Corpus.discover(args.corpus)
    except UnknownSidecar as exc:
        print(f"error: {exc}", file=stream)
        return 2

    if not corpus.fixtures:
        print(f"error: no fixtures under {corpus.root} -- is that a corpus checkout?", file=stream)
        return 2

    selected = corpus.select(args.filter)

    if args.list:
        for fixture in selected:
            print(fixture.name, file=stream)
        return 0

    if not selected:
        print(f"no fixture matches {' '.join(args.filter)}", file=stream)
        return 2

    waived = read_waivers(args.waivers) if args.waivers else {}
    print(_header(corpus, len(selected), args.binary, args.tz), file=stream)
    results = _run(selected, args, waived)
    report(results, args.verbose, stream)

    counts = Counter(r.outcome for r in results)
    print(", ".join(f"{counts[o]} {o.value}" for o in Outcome if counts[o]), file=stream)

    stale = sorted(set(waived) - {f.name for f in corpus.fixtures})
    for name in stale:
        print(f"warning: waiver for unknown fixture {name}", file=stream)

    return 0 if all(r.outcome.ok for r in results) and not stale else 1
