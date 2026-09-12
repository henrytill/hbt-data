"""Tests for what a failure is reported *against*.

A conformance harness is only useful if a failing fixture names the right
side.  These drive `check` with a stub executable rather than a real
implementation: the question is the diagnosis, not the parse.
"""

# Each test's name is its description; a docstring would restate it.
# enterContext is unittest's `with`; pylint does not recognize it as one.
# pylint: disable=missing-function-docstring,consider-using-with

from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path

from hbt.conformance.corpus import Corpus
from hbt.conformance.runner import Outcome, Result, check, check_all

DOCUMENT = """version: 0.1.0
length: 1
value:
- id: 0
  entity:
    uri: https://example.com/
    createdAt: 1700092800
    updatedAt: []
    names: []
    labels: []
  edges: []
"""


class Diagnosis(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / "markdown").mkdir()
        self.write("markdown/a.input.md", "# example\n")

    def write(self, name: str, content: str) -> None:
        (self.root / name).write_text(content, encoding="utf-8")

    def stub(self, output: str) -> Path:
        """An executable that prints `output` whatever it is asked for."""
        path = self.root / "stub"
        path.write_text(f'#!/bin/sh\ncat <<"EOF"\n{output}EOF\n', encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def fixture(self) -> Corpus:
        return Corpus.discover(self.root)

    def test_a_conforming_implementation_passes(self) -> None:
        self.write("markdown/a.expected.yaml", DOCUMENT)
        (fixture,) = self.fixture().fixtures
        self.assertIs(check(fixture, self.stub(DOCUMENT)).outcome, Outcome.PASS)

    def test_non_ascii_output_is_read_as_utf8_whatever_the_locale_is(self) -> None:
        """text=True would decode this with the locale's codec, and abort."""
        document = DOCUMENT.replace("names: []", "names:\n    - caf\u00e9")
        self.write("markdown/a.expected.yaml", document)
        (fixture,) = self.fixture().fixtures
        self.assertIs(check(fixture, self.stub(document)).outcome, Outcome.PASS)

    def test_a_malformed_expectation_is_reported_against_the_corpus(self) -> None:
        """Otherwise one corpus bug reads as the same bug in four parsers."""
        self.write("markdown/a.expected.yaml", DOCUMENT.replace("version: 0.1.0", "version: one"))
        (fixture,) = self.fixture().fixtures
        result = check(fixture, self.stub(DOCUMENT))
        self.assertIs(result.outcome, Outcome.FAIL)
        assert result.reason is not None
        self.assertIn("corpus error", result.reason)
        self.assertIn("a.expected.yaml", result.reason)

    def test_malformed_output_is_reported_against_the_implementation(self) -> None:
        self.write("markdown/a.expected.yaml", DOCUMENT)
        (fixture,) = self.fixture().fixtures
        result = check(fixture, self.stub(DOCUMENT.replace("version: 0.1.0", "version: one")))
        self.assertIs(result.outcome, Outcome.FAIL)
        assert result.reason is not None
        self.assertNotIn("corpus error", result.reason)
        self.assertIn("the output is not one", result.differences[0].render())


class Waivers(unittest.TestCase):
    """A waiver says an implementation is broken, and can say nothing else."""

    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.root / "markdown").mkdir()
        (self.root / "markdown" / "a.input.md").write_text("# example\n", encoding="utf-8")

    def write_expectation(self, content: str) -> None:
        (self.root / "markdown" / "a.expected.yaml").write_text(content, encoding="utf-8")

    def stub(self, output: str) -> Path:
        path = self.root / "stub"
        path.write_text(f'#!/bin/sh\ncat <<"EOF"\n{output}EOF\n', encoding="utf-8")
        path.chmod(path.stat().st_mode | stat.S_IXUSR)
        return path

    def check_all_waiving(self, output: str) -> list[Result]:
        fixtures = Corpus.discover(self.root).fixtures
        waived = {f.name: "broken upstream" for f in fixtures}
        return list(check_all(fixtures, self.stub(output), waived=waived))

    def test_an_ordinary_failure_is_waivable(self) -> None:
        self.write_expectation(DOCUMENT)
        (result,) = self.check_all_waiving(DOCUMENT.replace("example.com", "example.org"))
        self.assertIs(result.outcome, Outcome.XFAIL)

    def test_a_corpus_error_is_not_waivable(self) -> None:
        """A waiver lives in an implementation; a bad expectation lives here."""
        self.write_expectation(DOCUMENT.replace("version: 0.1.0", "version: one"))
        (result,) = self.check_all_waiving(DOCUMENT)
        self.assertIs(result.outcome, Outcome.FAIL)
        self.assertFalse(result.outcome.ok)
