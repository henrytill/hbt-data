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
import glob
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

INPUT_SUFFIX = ".input"
EXPECTED_MARKER = ".expected."

# Which input extensions each corpus directory holds.  The directory says
# which parser a fixture is about, the extension is what actually selects one
# -- the harness passes no `-f`, so the filename decides -- and a fixture
# filed under the wrong directory silently runs under some other parser and is
# counted in the wrong column of the report.
#
# hbt-ocaml states this pairing as `input_to_dir`/`format_to_ext` and hbt-hs
# as `categoryDir`/`formatExt`; both refuse a file that does not match.  This
# harness inferred the category from whatever was on disk, which is how a
# pinboard input under markdown/ passed as a markdown fixture.
CATEGORIES: dict[str, frozenset[str]] = {
    "html": frozenset({".html"}),
    "markdown": frozenset({".md"}),
    "pinboard/json": frozenset({".json"}),
    "pinboard/xml": frozenset({".xml"}),
}

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


class CorpusError(Exception):
    """A fixture the harness refuses to guess at.

    Discovery is filename-derived and has no manifest to check itself
    against, so a fixture whose files contradict each other has to be an
    error: the alternative is always a case that quietly asserts less than
    the files sitting beside it imply.
    """


class UnknownSidecar(CorpusError):
    """A ``<stem>.expected.*`` file that no output format claims.

    Almost always a typo or a format whose support was never added.  Reported
    rather than ignored, because the alternative is a fixture that silently
    checks less than the file sitting next to it implies.
    """


class IncompleteFixture(CorpusError):
    """A fixture that is only half present.

    An input with no expectation beside it is not a weaker fixture, it is a
    fixture that asserts nothing: the harness would run the parser and check
    only that it exited zero, which every implementation does for inputs
    nobody has ever looked at.  hbt-hs refuses the same shape, on the grounds
    that the missing half "would have been compared against the empty
    string".

    ``.expected.error`` is the one way to say that an input is not meant to
    produce a document, and it says why.

    The mirror image -- an expectation with no input -- is worse, because
    discovery walks the inputs: the file is not a fixture that asserts too
    little, it is a file nothing reads at all.  A renamed input leaves one
    behind, and the corpus goes on passing with a case silently gone.
    """


class MisfiledInput(CorpusError):
    """An input in a directory that does not hold inputs of its kind.

    Either the directory is not a category at all, or the category does not
    take that extension.  Neither can be honoured by guessing: the extension
    picks the parser and the directory picks the column of the report, so a
    mismatch is two different claims about one file.
    """


class CollidingInputs(CorpusError):
    """Two inputs in one directory that differ only in their extension.

    A fixture is named by its stem, so ``a.input.md`` and ``a.input.html``
    would both be ``markdown/a``: ``--list`` prints the name twice, one
    waiver silently covers both, and a failure cannot say which input
    produced it.  Renaming one is the fix; keying names on the full filename
    instead would put an extension into every waiver and every filter.
    """


class ContradictoryExpectations(CorpusError):
    """A fixture that must be rejected and also pins what it parses to.

    ``.expected.error`` says every implementation must refuse the input,
    which is checked before any output is compared -- so an output
    expectation beside it is never read.  The two cannot both be meant.
    """


@dataclass(frozen=True)
class Fixture:
    """One corpus case: an input, and what should become of it."""

    name: str
    input_path: Path
    #: Output format -> the file its output must match.  Empty only for a
    #: fixture whose input must be refused, which pins no output.
    expected: dict[str, Path]
    #: The reason the input must be refused, or ``None`` if it must parse.
    error: str | None
    #: The file that reason came from, kept so the report can name every file
    #: a fixture is made of rather than only the ones holding a document.
    error_path: Path | None = None

    @property
    def rejected(self) -> bool:
        """Whether every implementation must refuse this input."""
        return self.error is not None

    @property
    def files(self) -> tuple[Path, ...]:
        """Everything this fixture pins, in a stable order."""
        pinned = [self.expected[fmt] for fmt in sorted(self.expected)]
        return tuple(pinned or ([self.error_path] if self.error_path is not None else []))


def _reject_orphans(root: Path, named: set[str]) -> None:
    """Raise for any expectation that no input claims.

    Discovery is driven by ``*.input.*``, so a sidecar whose input is gone or
    was renamed is not read by anything and nothing says so -- the quiet half
    of a half-fixture.  hbt-hs catches the same shape from the other
    direction, refusing a stem that has one file of the pair.
    """
    for path in sorted(root.rglob("*.expected.*")):
        if ".git" in path.parts:
            continue
        stem = path.parent / path.name[: path.name.index(EXPECTED_MARKER)]
        if str(stem.relative_to(root)) not in named:
            raise IncompleteFixture(f"{path.relative_to(root)}: no input is named {stem.name}")


def _check_category(path: Path, category: str) -> None:
    """Raise unless ``path`` is an input the directory holding it takes."""
    extensions = CATEGORIES.get(category)
    if extensions is None:
        known = ", ".join(sorted(CATEGORIES))
        raise MisfiledInput(f"{path.name} is in {category}, which is not a corpus category ({known})")
    suffix = path.suffix
    if suffix not in extensions:
        takes = ", ".join(sorted(extensions))
        raise MisfiledInput(f"{path.name} is in {category}, which holds {takes} inputs")


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
    """Every ``<stem>.expected.*`` beside a fixture, keyed by its suffix.

    The stem is escaped before it goes into the pattern: a fixture named
    ``a[1]`` would otherwise be read as a character class, match nothing, and
    discover as a fixture with no expectations at all -- asserting only that
    the input parses, with no UnknownSidecar to say so.  Corpus names are all
    ``[a-z_]`` today, so this is latent, and it fails in exactly the quiet way
    this module refuses to fail elsewhere.
    """
    prefix = f"{stem.name}.expected."
    return {path.name[len(stem.name) :]: path for path in stem.parent.glob(glob.escape(prefix) + "*")}


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
        inputs: dict[str, Path] = {}
        for path in sorted(root.rglob(f"*{INPUT_SUFFIX}.*")):
            if ".git" in path.parts:
                continue
            stem = path.parent / path.name[: path.name.index(INPUT_SUFFIX)]
            name = str(stem.relative_to(root))
            _check_category(path, str(path.parent.relative_to(root)))
            first = inputs.setdefault(name, path)
            if first != path:
                raise CollidingInputs(f"{name}: {first.name} and {path.name} are two fixtures with one name")
            sidecars = _sidecars(stem)

            unknown = sorted(set(sidecars) - RECOGNIZED_SUFFIXES)
            if unknown:
                names = ", ".join(stem.name + suffix for suffix in unknown)
                raise UnknownSidecar(f"{stem.parent.relative_to(root)}: no output format claims {names}")

            error_path = sidecars.get(ERROR_SUFFIX)
            outputs = {fmt: sidecars[suffix] for fmt, suffix in EXPECTED_SUFFIXES.items() if suffix in sidecars}
            if error_path is None and not outputs:
                expectations = ", ".join(stem.name + suffix for suffix in sorted(RECOGNIZED_SUFFIXES))
                raise IncompleteFixture(f"{name}: {path.name} has nothing beside it -- expected one of {expectations}")
            if error_path is not None and outputs:
                names = ", ".join(sorted(path.name for path in outputs.values()))
                raise ContradictoryExpectations(
                    f"{stem.parent.relative_to(root)}: {error_path.name} says the input is refused,"
                    f" but {names} says what it parses to"
                )
            found.append(
                Fixture(
                    name=name,
                    input_path=path,
                    expected=outputs,
                    error=(
                        error_path.read_text(encoding="utf-8").strip() or "(no reason given)"
                        if error_path is not None
                        else None
                    ),
                    error_path=error_path,
                )
            )
        _reject_orphans(root, set(inputs))
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


def coverage(fixtures: Sequence[Fixture]) -> dict[str, int]:
    """How many of ``fixtures`` pin each output format.

    Over the fixtures actually selected rather than over the whole corpus:
    the count is reported beside a run, and a run that was filtered down to
    one markdown case has not checked nine HTML expectations.
    """
    counts = {fmt: 0 for fmt in EXPECTED_SUFFIXES}
    for fixture in fixtures:
        for fmt in fixture.expected:
            counts[fmt] += 1
    return counts


def categories(fixtures: Sequence[Fixture]) -> dict[str, int]:
    """How many of ``fixtures`` come from each top-level corpus directory.

    The input side of the same question :func:`coverage` answers for outputs,
    and not derivable from it: the corpus directories are ``html``,
    ``markdown`` and ``pinboard``, while the output formats are ``yaml`` and
    ``html``, so "9 html" alone says nothing about whether the markdown
    fixtures ran.  ``pinboard/xml`` and ``pinboard/json`` count as one
    category, which is how the parsers are organized.
    """
    counts: dict[str, int] = {}
    for fixture in fixtures:
        category = fixture.name.split("/", 1)[0]
        counts[category] = counts.get(category, 0) + 1
    return counts
