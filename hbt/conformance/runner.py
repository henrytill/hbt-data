"""Running one executable over the corpus.

The harness drives the CLI the way a user does -- ``hbt -t FORMAT FILE``, once
per output format the fixture pins -- and reads its standard output.  It does
not pass ``-f``: the four implementations spell the markdown format
differently (``md`` in hbt-rs, ``markdown`` in the other three), while all four
agree on detecting the format from the file extension, so extension detection
is both the portable route and the one the corpus filenames were built for.
That divergence is a real one -- there is no `-f` value that works on all
four -- and is tracked as henrytill/hbt-data#16.  It is worked around here
rather than papered over, and the cost of the workaround is that conformance
never exercises the `-f` path at all.

**The harness does not pin ``TZ``.**  hbt-ocaml's dune action pinned it to UTC,
and it would have been easy to inherit that here for all four.  But all four
are timezone-invariant today -- that is a property of the implementations, and
one worth keeping -- and a harness that pins the zone is a harness that cannot
notice the property being lost.  So the ambient zone is what runs, and a
developer outside UTC is checking something CI cannot.  ``--tz`` forces a zone
when reproducing a failure that only appears in one.
"""

from __future__ import annotations

import enum
import os
import subprocess
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import yaml

from hbt.conformance.corpus import Corpus, Fixture
from hbt.conformance.normalize import Difference, NormalizationError, compare_html, diff, normalize
from hbt.conformance.yaml_io import load_yaml

DEFAULT_TIMEOUT = 30.0


@dataclass(frozen=True)
class Format:
    """How one output format's bytes are read, and how two of them differ.

    One record rather than a table per operation: reading an expectation on
    its own -- so that a corpus file which is not what it claims is reported
    against the corpus -- and comparing two documents are the same knowledge
    about one format, and a third format should be one entry rather than an
    entry in each of two dicts.

    The two formats differ in what ``parse`` means, deliberately.  For YAML
    it is the data model, so quoting and key order are not differences; for
    HTML it is the bytes themselves, which is why ``parse`` is identity there
    rather than vacuous.  See :mod:`hbt.conformance.normalize`.
    """

    parse: Callable[[bytes], Any]
    diff: Callable[[Any, Any], list[Difference]]


FORMATS: dict[str, Format] = {
    "yaml": Format(parse=lambda raw: normalize(load_yaml(_decode(raw))), diff=diff),
    "html": Format(parse=lambda raw: raw, diff=compare_html),
}


class Outcome(enum.Enum):
    PASS = "pass"
    FAIL = "fail"
    XFAIL = "xfail"
    XPASS = "xpass"

    @property
    def ok(self) -> bool:
        """Whether this outcome lets the run succeed."""
        return self in (Outcome.PASS, Outcome.XFAIL)


@dataclass(frozen=True)
class Result:
    fixture: Fixture
    outcome: Outcome
    reason: str | None = None
    differences: tuple[Difference, ...] = field(default_factory=tuple)
    corpus_error: bool = False
    """Whether the failure is the corpus's rather than the implementation's."""

    def waive(self, reason: str) -> Result:
        """Reinterpret this result as one the caller expected to fail.

        Waivers live in the implementations rather than here: a fixture can
        then land in the corpus before four parsers are fixed, and this
        repository stays free of knowledge about who is currently broken.
        ``reason`` is the waiver's own record of why, carried into the report
        so a waived fixture names its owner instead of going quiet.

        A corpus error is never waivable.  It is the one failure that is not
        a statement about the implementation being run, so a waiver in one
        implementation's repository must not be able to silence a broken
        expectation file in this one -- that would let a corrupt fixture ride
        along at exit 0, which is the whole distinction :func:`_compare`
        draws the corpus errors out to make.
        """
        if self.corpus_error:
            return self
        if self.outcome is Outcome.FAIL:
            return Result(self.fixture, Outcome.XFAIL, f"{reason} [{self.reason}]", self.differences)
        if self.outcome is Outcome.PASS:
            return Result(self.fixture, Outcome.XPASS, f"{reason} -- but it passes; drop the waiver")
        return self

    def detail(self) -> list[str]:
        """The differences behind this result, as lines for a report to indent.

        Unindented, because where they sit is the report's business: beneath a
        row naming the fixture in one, beneath a cell naming the implementation
        in another.
        """
        return [line for difference in self.differences for line in difference.render().splitlines()]


def read_waivers(path: Path) -> dict[str, str]:
    """Fixture names a caller expects to fail, mapped to why.

    One per line, with the reason after ``#``.  The reason is kept rather than
    discarded: a waiver file is authored in an implementation's repository and
    read here, so a bare name would be a suppression with no recorded owner or
    exit condition -- and the concern it belongs to is what a conformance
    matrix has to key on.

    This format is a contract with four other repositories, which is why it is
    defined beside the runner that applies it rather than in the command line.
    """
    waivers: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        name, _, reason = line.partition("#")
        if name.strip():
            waivers[name.strip()] = reason.strip() or "no reason recorded"
    return waivers


def _run(binary: Path, fixture: Fixture, to: str, timeout: float, tz: str | None) -> subprocess.CompletedProcess[bytes]:
    """Run the executable over one fixture, capturing its output undecoded."""
    return subprocess.run(
        [str(binary), "-t", to, str(fixture.input_path)],
        capture_output=True,
        env=None if tz is None else dict(os.environ, TZ=tz),
        timeout=timeout,
        check=False,
    )


def _decode(raw: bytes) -> str:
    """The child's output as text, read as UTF-8 whatever the locale says.

    Not ``text=True``, which decodes with the locale's encoding: under a
    ``LANG``-less C locale that is ASCII, and four fixtures carry non-ASCII,
    so the harness died with a ``UnicodeDecodeError`` raised inside
    ``subprocess`` -- before any comparison, and outside the handlers here, so
    one unrepresentable byte aborted the whole run.  The expectations are read
    with an explicit encoding for the same reason: both sides of a comparison
    have to agree on what the bytes mean, and the corpus is UTF-8.

    ``errors="replace"`` rather than a raise, because output this harness
    cannot decode is a conformance failure of the implementation that wrote
    it, and it should be reported as a difference like any other.
    """
    return raw.decode("utf-8", errors="replace")


def check(fixture: Fixture, binary: Path, timeout: float = DEFAULT_TIMEOUT, tz: str | None = None) -> Result:
    """Run one fixture in every format it pins, and say whether it conformed."""
    # Discovery refuses a fixture with no expectation at all, so an empty
    # `expected` is a rejection fixture: run it as `-t yaml` to find out
    # whether the input is refused, and compare nothing.
    formats = sorted(fixture.expected) or ["yaml"]

    runs: dict[str, subprocess.CompletedProcess[bytes]] = {}
    for fmt in formats:
        try:
            runs[fmt] = _run(binary, fixture, fmt, timeout, tz)
        except subprocess.TimeoutExpired:
            return Result(fixture, Outcome.FAIL, f"-t {fmt} timed out after {timeout:g}s")
        except OSError as exc:
            return Result(fixture, Outcome.FAIL, f"could not run {binary}: {exc}")

    if fixture.rejected:
        return _check_rejected(fixture, runs)

    differences, failed, corpus_errors = _compare(fixture, formats, runs)
    if corpus_errors:
        reason = f"corpus error: {', '.join(corpus_errors)}"
        return Result(fixture, Outcome.FAIL, reason, tuple(differences), corpus_error=True)
    if differences:
        return Result(fixture, Outcome.FAIL, _summarize(failed, differences), tuple(differences))
    return Result(fixture, Outcome.PASS)


def _compare(
    fixture: Fixture, formats: list[str], runs: dict[str, subprocess.CompletedProcess[bytes]]
) -> tuple[list[Difference], list[str], list[str]]:
    """Hold each format's output to its expectation.

    Every format is compared, even after one of them has already failed: a
    run that stops at the first bad format cannot say whether the others
    diverged too, which is the question a conformance report exists to
    answer.

    Returns the differences, the formats that failed, and the expectation
    files that turned out not to be what they claim -- the last kept apart
    because a corpus error is not the implementation's failure.
    """
    differences: list[Difference] = []
    failed: list[str] = []
    corpus_errors: list[str] = []
    for fmt in formats:
        proc = runs[fmt]
        if proc.returncode != 0:
            failed.append(fmt)
            differences.append(Difference(f"$({fmt})", "output", _stderr(proc)))
            continue
        if fmt not in fixture.expected:
            continue
        sidecar = fixture.expected[fmt]
        # Bytes on both sides: the `-t html` rule is byte equality, and text
        # mode would translate a CRLF divergence out of existence before the
        # comparison saw it.
        parse = FORMATS[fmt].parse
        # The expectation is parsed on its own, so that a corpus file which is
        # not a valid Collection -- hand edited, or carrying a field added
        # upstream before the harness knew it -- is reported against the
        # corpus rather than failing every implementation at once with a
        # message that names no side.
        try:
            want = parse(sidecar.read_bytes())
        except (NormalizationError, yaml.YAMLError) as exc:
            failed.append(fmt)
            corpus_errors.append(sidecar.name)
            differences.append(Difference(f"$({fmt})", "a Collection", f"{sidecar.name} is not one: {exc}"))
            continue
        try:
            got = parse(proc.stdout)
        except (NormalizationError, yaml.YAMLError) as exc:
            failed.append(fmt)
            differences.append(Difference(f"$({fmt})", "a Collection", f"the output is not one: {exc}"))
            continue
        found = FORMATS[fmt].diff(want, got)
        if found:
            failed.append(fmt)
        differences.extend(found)
    return differences, failed, corpus_errors


def _check_rejected(fixture: Fixture, runs: dict[str, subprocess.CompletedProcess[bytes]]) -> Result:
    """A fixture whose input every implementation must refuse."""
    accepted = sorted(fmt for fmt, proc in runs.items() if proc.returncode == 0)
    if accepted:
        formats = ", ".join(f"-t {fmt}" for fmt in accepted)
        return Result(fixture, Outcome.FAIL, f"should have been rejected ({fixture.error}), but {formats} accepted it")
    return Result(fixture, Outcome.PASS)


def _summarize(failed: list[str], differences: list[Difference]) -> str:
    formats = ", ".join(f"-t {fmt}" for fmt in failed)
    return f"{len(differences)} difference(s) in {formats}"


def _stderr(proc: subprocess.CompletedProcess[bytes]) -> str:
    message = _decode(proc.stderr).strip().splitlines()
    detail = message[0] if message else "no diagnostic on stderr"
    return f"exited {proc.returncode}: {detail}"


def check_all(
    fixtures: Sequence[Fixture],
    binary: Path,
    timeout: float = DEFAULT_TIMEOUT,
    tz: str | None = None,
    jobs: int = 8,
    waived: Mapping[str, str] | None = None,
) -> list[Result]:
    """Check every fixture against one executable, waiving what the caller expects to fail.

    The entry point for anything holding an implementation to the corpus
    without wanting a printed report -- the cross-implementation matrix being
    the reason this package is importable at all. It takes what it needs
    rather than the command line's record, so a caller need not build one.

    Threads rather than a sequential loop: nearly all of the time is spent
    waiting on subprocesses. `map` yields in submission order, so the results
    are deterministic without any sorting.
    """
    expected_to_fail = waived or {}

    def run_one(fixture: Fixture) -> Result:
        return check(fixture, binary, timeout, tz)

    with ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        checked = pool.map(run_one, fixtures)
        return [r.waive(expected_to_fail[r.fixture.name]) if r.fixture.name in expected_to_fail else r for r in checked]


@dataclass(frozen=True)
class Run:
    """One executable held to one corpus, with everything that decides the verdict.

    A record rather than rules left in a command's body: the command line, the
    cross-implementation matrix in hbt-analysis and any machine-readable report
    all need the same verdict, and each restating how it is reached is how they
    come to disagree about a fixture.
    """

    corpus: Corpus
    results: tuple[Result, ...]
    stale: tuple[str, ...]
    """Waivers naming no fixture in the corpus.

    A stale waiver fails the run: it is either a suppression whose fixture has
    gone, or a typo that suppresses nothing while looking as though it does.
    """

    @property
    def ok(self) -> bool:
        """Whether the run conformed: every outcome acceptable, and no waiver stale.

        A run over no fixtures conforms, vacuously, and its summary is empty.
        Whether an empty selection is a mistake depends on why it is empty,
        which only the caller knows -- see :func:`check_corpus`.
        """
        return all(r.outcome.ok for r in self.results) and not self.stale

    def summary(self) -> str:
        """How many fixtures ended each way, e.g. ``36 pass, 1 xfail``."""
        counts = Counter(r.outcome for r in self.results)
        return ", ".join(f"{counts[o]} {o.value}" for o in Outcome if counts[o])


def check_corpus(  # pylint: disable=too-many-arguments,too-many-positional-arguments
    corpus: Corpus,
    binary: Path,
    fixtures: Sequence[Fixture] | None = None,
    waivers: Mapping[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    tz: str | None = None,
    jobs: int = 8,
) -> Run:
    """Check ``fixtures`` -- all of ``corpus`` if not given -- and reach a verdict.

    The entry point for holding an implementation to the corpus from Python.
    Staleness is judged against the whole corpus rather than the selection, so
    a filtered run does not report a waiver for a fixture it merely did not
    run.

    An empty ``fixtures`` is not refused, unlike an empty corpus, and the
    :class:`Run` it returns is ``ok``.  The two are different questions.  A
    corpus with no fixtures is always a broken checkout or a wrong path, so
    :meth:`Corpus.discover` refuses it for every caller.  A selection with no
    fixtures is a statement about filters: the command treats it as a usage
    error, while a caller checking several corpora pinned at different
    revisions may legitimately select nothing from one of them.  So refusing
    it is the caller's decision, made before calling this.
    """
    waived = waivers or {}
    selected = corpus.fixtures if fixtures is None else fixtures
    results = check_all(selected, binary, timeout, tz, jobs, waived)
    stale = tuple(sorted(set(waived) - {f.name for f in corpus.fixtures}))
    return Run(corpus, tuple(results), stale)
