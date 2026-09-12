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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

from hbt.conformance.corpus import Fixture
from hbt.conformance.normalize import Difference, NormalizationError, compare, compare_html
from hbt.conformance.yaml_io import load_yaml

DEFAULT_TIMEOUT = 30.0

#: How each output format's bytes are compared against its expectation.  The
#: two rules differ deliberately -- see :mod:`hbt.conformance.normalize`.
COMPARATORS: dict[str, Callable[[bytes, bytes], list[Difference]]] = {
    "yaml": lambda expected, actual: compare(load_yaml(_decode(expected)), load_yaml(_decode(actual))),
    "html": compare_html,
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

    def waive(self, reason: str) -> Result:
        """Reinterpret this result as one the caller expected to fail.

        Waivers live in the implementations rather than here: a fixture can
        then land in the corpus before four parsers are fixed, and this
        repository stays free of knowledge about who is currently broken.
        ``reason`` is the waiver's own record of why, carried into the report
        so a waived fixture names its owner instead of going quiet.
        """
        if self.outcome is Outcome.FAIL:
            return Result(self.fixture, Outcome.XFAIL, f"{reason} [{self.reason}]", self.differences)
        if self.outcome is Outcome.PASS:
            return Result(self.fixture, Outcome.XPASS, f"{reason} -- but it passes; drop the waiver")
        return self


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

    # Every format is compared, even after one of them has already failed: a
    # run that stops at the first bad format cannot say whether the others
    # diverged too, which is the question a conformance report exists to
    # answer.
    differences: list[Difference] = []
    failed: list[str] = []
    for fmt in formats:
        proc = runs[fmt]
        if proc.returncode != 0:
            failed.append(fmt)
            differences.append(Difference(f"$({fmt})", "output", _stderr(proc)))
            continue
        if fmt not in fixture.expected:
            continue
        # Bytes on both sides: the `-t html` rule is byte equality, and
        # text mode would translate a CRLF divergence out of existence
        # before the comparison saw it.
        expected = fixture.expected[fmt].read_bytes()
        try:
            found = COMPARATORS[fmt](expected, proc.stdout)
        except (NormalizationError, yaml.YAMLError) as exc:
            failed.append(fmt)
            differences.append(Difference(f"$({fmt})", "a Collection", str(exc)))
            continue
        if found:
            failed.append(fmt)
        differences.extend(found)

    if differences:
        return Result(fixture, Outcome.FAIL, _summarize(failed, differences), tuple(differences))
    return Result(fixture, Outcome.PASS)


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
