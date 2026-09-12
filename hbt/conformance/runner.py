"""Running one executable over the corpus.

The harness drives the CLI the way a user does -- ``hbt -t yaml FILE``, and
``hbt -t html FILE`` for the fixtures that pin a rendered bookmark file -- and
reads its standard output.  It does not pass ``-f``: the four implementations
spell the markdown format differently (``md`` in hbt-rs, ``markdown`` in the
other three), while all four agree on detecting the format from the file
extension, so extension detection is both the portable route and the one the
corpus filenames were built for.

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
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from hbt.conformance.corpus import Fixture
from hbt.conformance.normalize import Difference, NormalizationError, compare, compare_html

DEFAULT_TIMEOUT = 30.0


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

    def waive(self) -> Result:
        """Reinterpret this result as one the caller expected to fail.

        Waivers live in the implementations rather than here: a fixture can
        then land in the corpus before four parsers are fixed, and this
        repository stays free of knowledge about who is currently broken.
        """
        if self.outcome is Outcome.FAIL:
            return Result(self.fixture, Outcome.XFAIL, self.reason, self.differences)
        if self.outcome is Outcome.PASS:
            return Result(self.fixture, Outcome.XPASS, "waived, but passes -- drop the waiver")
        return self


def _run(
    binary: Path, fixture: Fixture, timeout: float, to: str = "yaml", tz: str | None = None
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ) if tz is None else dict(os.environ, TZ=tz)
    return subprocess.run(
        [str(binary), "-t", to, str(fixture.input_path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout,
        check=False,
    )


def check(  # pylint: disable=too-many-return-statements
    fixture: Fixture, binary: Path, timeout: float = DEFAULT_TIMEOUT, tz: str | None = None
) -> Result:
    """Run one fixture and say whether the executable conformed.

    The returns are a list of the distinct ways a fixture can end, which reads
    better flat than nested; each one is a separate verdict, not a branch on
    the way to a shared conclusion.
    """
    try:
        proc = _run(binary, fixture, timeout, tz=tz)
    except subprocess.TimeoutExpired:
        return Result(fixture, Outcome.FAIL, f"timed out after {timeout:g}s")
    except OSError as exc:
        return Result(fixture, Outcome.FAIL, f"could not run {binary}: {exc}")

    if fixture.rejected:
        if proc.returncode != 0:
            return Result(fixture, Outcome.PASS)
        return Result(fixture, Outcome.FAIL, "input should have been rejected, but was accepted")

    if fixture.expected_path is None:
        # No expectation recorded: the fixture only asserts that the input
        # parses at all.  Inputs with no expected file are how the corpus
        # carries cases whose output is not yet agreed on.
        if proc.returncode == 0:
            return Result(fixture, Outcome.PASS)
        return Result(fixture, Outcome.FAIL, _stderr(proc))

    if proc.returncode != 0:
        return Result(fixture, Outcome.FAIL, _stderr(proc))

    try:
        actual = yaml.safe_load(proc.stdout)
    except yaml.YAMLError as exc:
        return Result(fixture, Outcome.FAIL, f"output is not YAML: {exc}")

    expected = yaml.safe_load(fixture.expected_path.read_text(encoding="utf-8"))

    try:
        differences = compare(expected, actual)
    except NormalizationError as exc:
        return Result(fixture, Outcome.FAIL, str(exc))

    if fixture.expected_html_path is not None:
        differences = differences + _check_html(fixture, fixture.expected_html_path, binary, timeout, tz)

    if differences:
        return Result(fixture, Outcome.FAIL, f"{len(differences)} difference(s)", tuple(differences))
    return Result(fixture, Outcome.PASS)


def _check_html(
    fixture: Fixture, expected_path: Path, binary: Path, timeout: float, tz: str | None = None
) -> list[Difference]:
    """Compare ``-t html`` against the fixture's rendered bookmark file."""
    try:
        proc = _run(binary, fixture, timeout, to="html", tz=tz)
    except (subprocess.TimeoutExpired, OSError) as exc:
        return [Difference("$html", "a rendered bookmark file", f"could not produce one: {exc}")]
    if proc.returncode != 0:
        return [Difference("$html", "a rendered bookmark file", _stderr(proc))]
    return compare_html(expected_path.read_text(encoding="utf-8"), proc.stdout)


def _stderr(proc: subprocess.CompletedProcess[str]) -> str:
    message = proc.stderr.strip().splitlines()
    detail = message[0] if message else "no diagnostic on stderr"
    return f"exited {proc.returncode}: {detail}"
