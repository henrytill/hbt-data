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
from hbt.conformance.corpus import Corpus, CorpusError, Fixture, revision
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
    """What is being checked, and against what, one fact to a line.

    Only what the table beneath cannot say.  Each row names its category and
    lists the expectations it was held to, so a coverage breakdown in the
    header was a workaround for a table that named neither -- but the
    denominator is not in the rows, and a filtered run should say what it
    filtered from.

    The revision is here because a stale pin otherwise surfaces as dozens of
    opaque failures.  The binary distinguishes two runs in a CI log, and is
    usually a store path wider than a terminal, which is why this is a keyed
    block and not a sentence.  TZ appears only when one was forced: the
    ambient zone is the default, and naming it would imply the harness had
    pinned it.
    """
    rows = [
        ("corpus", revision(corpus.root)),
        ("fixtures", f"{len(selected)} of {len(corpus.fixtures)}"),
        ("binary", str(binary)),
    ]
    if tz is not None:
        rows.append(("TZ", tz))
    width = max(len(key) for key, _ in rows)
    return "\n".join(f"{key.ljust(width)}  {value}" for key, value in rows)


def _suffix(fixture: Fixture, path: Path) -> str:
    """What ``path`` adds to the fixture's stem, e.g. ``.input.md``."""
    return path.name[len(Path(fixture.name).name) :]


def report(results: Sequence[Result], quiet: bool, out: TextIO) -> int:
    """Print one line per fixture, with any differences beneath it.

    Every fixture is named, passes included, because the four suites this
    replaces all did: `cargo test`, `go test -v`, dune and tasty each said
    which cases ran, and a harness that prints only a total moves "did my
    fixture actually run?" back into a `--list` invocation.  `--quiet` is for
    a caller that wants only what failed.

    The stem is its own column and the files are the suffixes beside it.
    Printing whole paths says the stem twice a row, which in columns is most
    of the width; printing only the stem leaves out which input was parsed
    and which expectations it was held to -- the HTML fixtures pin two, and
    nothing else in the report distinguishes them from the twenty-eight that
    pin one.  Splitting them says both once.

    The stem column is also the fixture's name, which is what ``--waivers``
    entries and filter arguments take, so a failing row can be copied into
    either.

    Returns how many rows it printed, so the caller can tell a run that said
    nothing from one that did and space the report accordingly.

    Five columns, padded to the widest row actually printed, in an order that
    also suits a filter: outcome, name, input, expectations, and the reason
    last because it is the only field with spaces in it.  The expectations
    are comma-joined without a space, so every row has the same number of
    whitespace-separated fields and ``awk '$1 == "FAIL" {print $2}'`` prints
    a list of names that can be fed straight back in.  Alignment is a width
    pass and str.ljust; a table library would be a dependency in four
    implementations' closures for five columns.
    """
    rows = [
        (
            result.outcome.value.upper(),
            result.fixture.name,
            _suffix(result.fixture, result.fixture.input_path),
            ",".join(_suffix(result.fixture, path) for path in result.fixture.files),
            result.reason or "",
            result.differences,
        )
        for result in results
        if not (result.outcome is Outcome.PASS and quiet)
    ]
    widths = [max((len(row[i]) for row in rows), default=0) for i in range(4)]
    for *cells, reason, differences in rows:
        line = "  ".join(cell.ljust(width) for cell, width in zip(cells, widths))
        print(f"{line}  {reason}".rstrip(), file=out)
        for difference in differences:
            for line in difference.render().splitlines():
                print(f"    {line}", file=out)
    return len(rows)


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
    # Blank lines rather than a rule: the report is three blocks -- what ran,
    # what each fixture did, what it adds up to -- and a rule would be a
    # fourth thing for a filter to skip past.
    print(file=out)
    if report(results, options.quiet, out):
        print(file=out)

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
