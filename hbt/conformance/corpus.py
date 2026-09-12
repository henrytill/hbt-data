"""Finding the fixtures, and saying which revision of them is being used.

The corpus is located by walking up from this file rather than by an
environment variable or a command-line path, so ``python3 -m hbt.conformance
--binary ...`` works from a bare checkout with nothing configured.  That is
also why the corpus stays a git submodule in each implementation: a store path
would arrive without the git metadata :func:`revision` reads, and a stale pin
would go back to surfacing as dozens of opaque failures instead of one line.

There is no manifest.  Deriving the cases from the filenames is the mechanism
being consolidated out of four languages, and a manifest would be a fifth thing
to keep in sync.  The one thing a manifest would buy -- catching a sidecar that
nobody reads -- is bought instead by :class:`UnknownSidecar`: a file named
``<stem>.expected.something-else`` is an error rather than a fixture that
quietly asserts less than its author thought.
"""

from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass
from pathlib import Path

INPUT_SUFFIX = ".input"

# An expectation is keyed by the `-t` format that produces it.  Every fixture
# pins `-t yaml`; only the nine HTML fixtures pin `-t html`, which is a second
# output format and a second way for the four to diverge.  A third format would
# be another entry here and nothing else.
EXPECTED_SUFFIXES = {
    "yaml": ".expected.yaml",
    "html": ".expected.html",
}

# A fixture the parsers must refuse.  The corpus cannot express rejection as an
# expected document, so it is expressed as the absence of one plus this file,
# whose contents name the reason; see henrytill/hbt-data#11.
ERROR_SUFFIX = ".expected.error"

RECOGNIZED_SUFFIXES = frozenset(EXPECTED_SUFFIXES.values()) | {ERROR_SUFFIX}


class UnknownSidecar(Exception):
    """A ``<stem>.expected.*`` file that no output format claims.

    Almost always a typo or a format whose support was never added.  Reported
    rather than ignored, because the alternative is a fixture that silently
    checks less than the file sitting next to it implies.
    """


@dataclass(frozen=True)
class Fixture:
    """One corpus case: an input, and what should become of it."""

    name: str
    input_path: Path
    #: Output format -> the file its output must match.  Empty for a fixture
    #: that only asserts that the input parses at all.
    expected: dict[str, Path]
    #: The reason the input must be refused, or ``None`` if it must parse.
    error: str | None

    @property
    def rejected(self) -> bool:
        """Whether every implementation must refuse this input."""
        return self.error is not None


def _root() -> Path:
    # hbt/conformance/corpus.py -> the repository root.
    return Path(__file__).resolve().parents[2]


def revision(root: Path | None = None) -> str:
    """The corpus revision, or ``"unknown"`` outside a git checkout."""
    root = root or _root()
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return "unknown"
    return proc.stdout.strip() if proc.returncode == 0 else "unknown"


def _sidecars(stem: Path) -> dict[str, Path]:
    """Every ``<stem>.expected.*`` beside a fixture, keyed by its suffix."""
    prefix = f"{stem.name}.expected."
    return {path.name[len(stem.name) :]: path for path in stem.parent.glob(f"{prefix}*")}


@dataclass(frozen=True)
class Corpus:
    """The fixtures, in a stable order."""

    root: Path
    fixtures: tuple[Fixture, ...]

    @classmethod
    def discover(cls, root: Path | None = None) -> Corpus:
        """Every fixture under ``root``, sorted by name.

        Raises :class:`UnknownSidecar` if a fixture carries an expectation in
        a format the harness does not know how to check.
        """
        root = (root or _root()).resolve()
        found: list[Fixture] = []
        for path in sorted(root.rglob(f"*{INPUT_SUFFIX}.*")):
            if ".git" in path.parts:
                continue
            stem = path.parent / path.name[: path.name.index(INPUT_SUFFIX)]
            sidecars = _sidecars(stem)

            unknown = sorted(set(sidecars) - RECOGNIZED_SUFFIXES)
            if unknown:
                names = ", ".join(stem.name + suffix for suffix in unknown)
                raise UnknownSidecar(f"{stem.parent.relative_to(root)}: no output format claims {names}")

            error_path = sidecars.get(ERROR_SUFFIX)
            found.append(
                Fixture(
                    name=str(stem.relative_to(root)),
                    input_path=path,
                    expected={fmt: sidecars[s] for fmt, s in EXPECTED_SUFFIXES.items() if s in sidecars},
                    error=(
                        error_path.read_text(encoding="utf-8").strip() or "(no reason given)"
                        if error_path is not None
                        else None
                    ),
                )
            )
        return cls(root=root, fixtures=tuple(found))

    def select(self, patterns: list[str]) -> list[Fixture]:
        """Fixtures whose name matches any of ``patterns``.

        A pattern is a glob anchored nowhere, so ``markdown/basic``, ``basic``
        and ``markdown/*`` all pick out something useful.  Single-case
        selection is not a convenience here: deleting the generated suites
        takes ``cargo test -p hbt-test --test parsing markdown::test_basic``
        and its three equivalents with it, and this is what replaces them.
        """
        if not patterns:
            return list(self.fixtures)
        return [f for f in self.fixtures if any(fnmatch.fnmatch(f.name, f"*{p}*") for p in patterns)]

    def coverage(self) -> dict[str, int]:
        """How many fixtures pin each output format."""
        counts = {fmt: 0 for fmt in EXPECTED_SUFFIXES}
        for fixture in self.fixtures:
            for fmt in fixture.expected:
                counts[fmt] += 1
        return counts
