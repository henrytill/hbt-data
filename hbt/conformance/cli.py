"""``python3 -m hbt.conformance --binary path/to/hbt``."""

from __future__ import annotations

import io
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence, TextIO, cast

import click

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


@dataclass(frozen=True)
class Options:  # pylint: disable=too-many-instance-attributes
    """Everything a run is told, in the order the command line states it.

    A record rather than the parser's own namespace: `run` is called by the
    command and by anything driving the harness in-process, and neither
    should have to know which library parsed the arguments.
    """

    binary: Path
    corpus: Path | None = None
    waivers: Path | None = None
    timeout: float = DEFAULT_TIMEOUT
    tz: str | None = None
    jobs: int = 8
    list_only: bool = False
    quiet: bool = False
    patterns: tuple[str, ...] = field(default_factory=tuple)


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


def _check_all(fixtures: Sequence[Fixture], options: Options, waived: dict[str, str]) -> list[Result]:
    """Check every fixture, waiving the ones the caller expects to fail.

    Threads rather than a sequential loop: nearly all of the time is spent
    waiting on subprocesses.  `map` yields in submission order, so the report
    stays deterministic without any sorting.
    """

    def run_one(fixture: Fixture) -> Result:
        return check(fixture, options.binary, options.timeout, options.tz)

    with ThreadPoolExecutor(max_workers=options.jobs) as pool:
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


def run(options: Options, out: TextIO) -> int:
    """Run the selected fixtures; 0 if every one of them conformed."""
    try:
        corpus = Corpus.discover(options.corpus)
    except CorpusError as exc:
        print(f"error: {exc}", file=out)
        return 2

    if not corpus.fixtures:
        print(f"error: no fixtures under {corpus.root} -- is that a corpus checkout?", file=out)
        return 2

    selected = corpus.select(list(options.patterns))

    if options.list_only:
        for fixture in selected:
            print(fixture.name, file=out)
        return 0

    if not selected:
        print(f"no fixture matches {' '.join(options.patterns)}", file=out)
        return 2

    waived = read_waivers(options.waivers) if options.waivers else {}
    print(_header(corpus, selected, options.binary, options.tz), file=out)
    results = _check_all(selected, options, waived)
    report(results, options.quiet, out)

    counts = Counter(r.outcome for r in results)
    print(", ".join(f"{counts[o]} {o.value}" for o in Outcome if counts[o]), file=out)

    stale = sorted(set(waived) - {f.name for f in corpus.fixtures})
    for name in stale:
        print(f"warning: waiver for unknown fixture {name}", file=out)

    return 0 if all(r.outcome.ok for r in results) and not stale else 1


@click.command(context_settings={"help_option_names": ["-h", "--help"]})
# exists=True on the paths: a missing binary would otherwise be reported once
# per fixture as "could not run", which is 37 lines saying one thing about the
# caller rather than anything about conformance.
@click.option(
    "--binary",
    required=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="the hbt executable to test",
)
@click.option(
    "--corpus",
    "corpus_root",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    help="corpus root (defaults to this checkout)",
)
@click.option(
    "--waivers",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="file of fixture names expected to fail",
)
@click.option(
    "--timeout",
    type=float,
    default=DEFAULT_TIMEOUT,
    show_default=True,
    help="per-fixture timeout in seconds",
)
@click.option(
    "--tz",
    help="run under this timezone instead of the ambient one",
)
@click.option(
    "-j",
    "--jobs",
    type=click.IntRange(min=1),
    default=8,
    show_default=True,
    help="fixtures to run at once",
)
@click.option(
    "-l",
    "--list",
    "list_only",
    is_flag=True,
    help="list the selected fixtures and exit",
)
@click.option(
    "-q",
    "--quiet",
    is_flag=True,
    help="report only what did not pass",
)
@click.version_option(__version__, "--version", prog_name="hbt-conformance")
@click.argument("patterns", nargs=-1, metavar="[FILTER]...")
@click.pass_context
def cli(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    ctx: click.Context,
    binary: Path,
    corpus_root: Path | None,
    waivers: Path | None,
    timeout: float,
    tz: str | None,
    jobs: int,
    list_only: bool,
    quiet: bool,
    patterns: tuple[str, ...],
) -> None:
    """Run the hbt corpus against an hbt executable.

    FILTER selects fixtures by name, substring or glob; every fixture runs if
    none is given.
    """
    options = Options(binary, corpus_root, waivers, timeout, tz, jobs, list_only, quiet, patterns)
    ctx.exit(run(options, _utf8(sys.stdout)))
