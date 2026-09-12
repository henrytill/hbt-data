"""Finding the fixtures, and saying which revision of them is being used.

The corpus is located by walking up from this file rather than by an
environment variable or a command-line path, so ``python3 -m hbt.conformance
--binary ...`` works from a bare checkout with nothing configured.  That is
also why the corpus stays a git submodule in each implementation: a store path
would arrive without the git metadata :func:`revision` reads, and a stale pin
would go back to surfacing as dozens of opaque failures instead of one line.
"""

from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass
from pathlib import Path

INPUT_SUFFIX = ".input"
EXPECTED_YAML_SUFFIX = ".expected.yaml"
# Only the HTML fixtures carry one: it pins the rendered Netscape bookmark file
# that `-t html` produces, which is a second output format and a second way for
# the four to diverge.
EXPECTED_HTML_SUFFIX = ".expected.html"
# A fixture the parsers must refuse.  The corpus cannot express rejection as an
# expected document, so it is expressed as the absence of one plus this marker;
# see henrytill/hbt-data#11.
REJECT_SUFFIX = ".expected.reject"


@dataclass(frozen=True)
class Fixture:
    """One corpus case: an input, and what should become of it."""

    name: str
    input_path: Path
    expected_path: Path | None
    expected_html_path: Path | None
    rejected: bool

    @property
    def category(self) -> str:
        """The directory the fixture lives in, or "" at the corpus root."""
        return self.name.rsplit("/", 1)[0] if "/" in self.name else ""


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


@dataclass(frozen=True)
class Corpus:
    """The fixtures, in a stable order."""

    root: Path
    fixtures: tuple[Fixture, ...]

    @classmethod
    def discover(cls, root: Path | None = None) -> Corpus:
        """Every fixture under ``root``, sorted by name."""
        root = (root or _root()).resolve()
        found: list[Fixture] = []
        for path in sorted(root.rglob(f"*{INPUT_SUFFIX}.*")):
            if ".git" in path.parts:
                continue
            stem = path.parent / path.name[: path.name.index(INPUT_SUFFIX)]
            expected = stem.with_name(stem.name + EXPECTED_YAML_SUFFIX)
            expected_html = stem.with_name(stem.name + EXPECTED_HTML_SUFFIX)
            reject = stem.with_name(stem.name + REJECT_SUFFIX)
            found.append(
                Fixture(
                    name=str(stem.relative_to(root)),
                    input_path=path,
                    expected_path=expected if expected.exists() else None,
                    expected_html_path=expected_html if expected_html.exists() else None,
                    rejected=reject.exists(),
                )
            )
        return cls(root=root, fixtures=tuple(found))

    def select(self, patterns: list[str]) -> list[Fixture]:
        """Fixtures matching any of ``patterns``.

        A pattern matches as a glob against the fixture name, and also as a
        plain substring, so ``markdown/basic``, ``basic`` and ``markdown/*``
        all pick out something useful.  Single-case selection is not a
        convenience here: deleting the generated suites takes ``cargo test -p
        hbt-test --test parsing markdown::test_basic`` and its three
        equivalents with it, and this is what replaces them.
        """
        if not patterns:
            return list(self.fixtures)
        return [f for f in self.fixtures if any(fnmatch.fnmatch(f.name, p) or p in f.name for p in patterns)]
