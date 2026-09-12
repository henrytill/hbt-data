"""``python3 -m hbt.conformance --binary path/to/hbt``."""

from __future__ import annotations

import argparse
import io
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Sequence, TextIO, cast

from hbt.conformance import __version__
from hbt.conformance.corpus import Corpus, CorpusError, Fixture, categories, coverage, revision
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
    parser.add_argument("-q", "--quiet", action="store_true", help="report only what did not pass")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("filter", nargs="*", help="fixture name, substring, or glob; all fixtures if omitted")
    return parser


def _header(corpus: Corpus, selected: Sequence[Fixture], binary: Path, tz: str | None) -> str:
    """The one line that says what is being checked, and against what.

    Both halves of the coverage are stated rather than left implicit.  The
    corpus directories say which parsers were exercised -- a run reporting
    only "9 html, 37 yaml" reads as though the markdown and pinboard fixtures
    never ran -- and the output formats say which formatters were, since only
    the HTML fixtures pin `-t html` and "37 pass" alone would not say that the
    HTML formatter went unchecked on the other 28 inputs.

    Counted over the selected fixtures, not the corpus: a run filtered down to
    one markdown case has not checked nine HTML expectations, and saying it
    had was the more misleading half of the old line.  The corpus revision is
    here because a stale pin otherwise surfaces as dozens of opaque failures.
    """
    inputs = ", ".join(f"{n} {category}" for category, n in sorted(categories(selected).items()))
    formats = ", ".join(f"{n} -t {fmt}" for fmt, n in sorted(coverage(selected).items()) if n)
    zone = f" \u2022 TZ={tz}" if tz else ""
    plural = "" if len(selected) == 1 else "s"
    return (
        f"corpus {revision(corpus.root)} \u2022 {len(selected)} fixture{plural} ({inputs})"
        f" \u2022 {formats} \u2022 binary {binary}{zone}"
    )


def report(results: Sequence[Result], quiet: bool, out: TextIO) -> None:
    """Print one line per fixture, with any differences beneath it.

    Every fixture is named, passes included, because the four suites this
    replaces all did: `cargo test`, `go test -v`, dune and tasty each said
    which cases ran, and a harness that prints only a total moves "did my
    fixture actually run?" back into a `--list` invocation.  `--quiet` is for
    a caller that wants only what failed.
    """
    for result in results:
        if result.outcome is Outcome.PASS and quiet:
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


def _utf8(stream: TextIO) -> TextIO:
    """``stream``, decoupled from the locale's encoding.

    Everything printed here is UTF-8 by construction -- the header's
    separators, and the fixture content quoted in a difference -- while a
    ``LANG``-less C locale gives stdout the ASCII codec.  Printing the header
    then raised ``UnicodeEncodeError`` before a single fixture ran, so a bare
    CI runner got a traceback instead of a conformance result.  Reconfiguring
    is preferred to spelling the report in ASCII: a difference quotes whatever
    the corpus and the implementation contain, which no amount of restraint
    here keeps to ASCII.
    """
    if isinstance(stream, io.TextIOWrapper) and (stream.encoding or "").lower().replace("-", "") != "utf8":
        stream.reconfigure(encoding="utf-8", errors="replace")
    # isinstance() narrows to TextIOWrapper[Unknown], which pyright will not
    # widen back to the declared return type on its own.
    return cast(TextIO, stream)


def main(argv: Sequence[str] | None = None, out: TextIO | None = None) -> int:
    """Run the selected fixtures; 0 if every one of them conformed."""
    args = build_parser().parse_args(argv)
    stream: TextIO = out if out is not None else _utf8(sys.stdout)

    try:
        corpus = Corpus.discover(args.corpus)
    except CorpusError as exc:
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
    print(_header(corpus, selected, args.binary, args.tz), file=stream)
    results = _run(selected, args, waived)
    report(results, args.quiet, stream)

    counts = Counter(r.outcome for r in results)
    print(", ".join(f"{counts[o]} {o.value}" for o in Outcome if counts[o]), file=stream)

    stale = sorted(set(waived) - {f.name for f in corpus.fixtures})
    for name in stale:
        print(f"warning: waiver for unknown fixture {name}", file=stream)

    return 0 if all(r.outcome.ok for r in results) and not stale else 1
