"""Tests for how the corpus is read off the filesystem.

There is no manifest, so the directory layout is the only statement of what the
corpus contains.  These pin the places where that could go wrong quietly.
"""

# Each test's name is its description; a docstring would restate it.
# enterContext is unittest's `with`; pylint does not recognize it as one.
# pylint: disable=missing-function-docstring,consider-using-with

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hbt.conformance.corpus import ContradictoryExpectations, Corpus, UnknownSidecar, categories, coverage


class Discovery(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def write(self, name: str, content: str = "") -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_expectations_are_keyed_by_output_format(self) -> None:
        self.write("markdown/a.input.md")
        self.write("markdown/a.expected.yaml")
        self.write("markdown/a.expected.html")
        (fixture,) = Corpus.discover(self.root).fixtures
        self.assertEqual(sorted(fixture.expected), ["html", "yaml"])
        self.assertFalse(fixture.rejected)

    def test_a_fixture_may_pin_no_output_at_all(self) -> None:
        self.write("markdown/a.input.md")
        (fixture,) = Corpus.discover(self.root).fixtures
        self.assertEqual(fixture.expected, {})
        self.assertIsNone(fixture.error)

    def test_an_error_file_makes_the_fixture_a_rejection(self) -> None:
        self.write("markdown/a.input.md")
        self.write("markdown/a.expected.error", "missing-date\n")
        (fixture,) = Corpus.discover(self.root).fixtures
        self.assertTrue(fixture.rejected)
        self.assertEqual(fixture.error, "missing-date")

    def test_an_unrecognized_sidecar_is_an_error_not_a_shrug(self) -> None:
        """A typo must not silently downgrade a fixture to checking nothing."""
        self.write("markdown/a.input.md")
        self.write("markdown/a.expected.yml")
        with self.assertRaisesRegex(UnknownSidecar, "a.expected.yml"):
            Corpus.discover(self.root)

    def test_a_rejection_fixture_may_not_also_pin_an_output(self) -> None:
        """The rejection is checked first, so the expectation is never read."""
        self.write("markdown/a.input.md")
        self.write("markdown/a.expected.error", "missing-date")
        self.write("markdown/a.expected.yaml")
        with self.assertRaisesRegex(ContradictoryExpectations, "a.expected.yaml"):
            Corpus.discover(self.root)

    def test_a_glob_metacharacter_in_a_name_does_not_hide_its_sidecars(self) -> None:
        """Unescaped, `a[1]` reads as a character class and matches nothing."""
        self.write("markdown/a[1].input.md")
        self.write("markdown/a[1].expected.yaml")
        (fixture,) = Corpus.discover(self.root).fixtures
        self.assertEqual(set(fixture.expected), {"yaml"})

    def test_coverage_counts_fixtures_per_format(self) -> None:
        self.write("markdown/a.input.md")
        self.write("markdown/a.expected.yaml")
        self.write("markdown/b.input.md")
        self.write("markdown/b.expected.yaml")
        self.write("markdown/b.expected.html")
        self.assertEqual(coverage(Corpus.discover(self.root).fixtures), {"yaml": 2, "html": 1})

    def test_coverage_counts_the_fixtures_it_is_given_not_the_corpus(self) -> None:
        """The header reports a run, and a filtered run checked less."""
        self.write("markdown/a.input.md")
        self.write("markdown/a.expected.yaml")
        self.write("html/b.input.html")
        self.write("html/b.expected.yaml")
        self.write("html/b.expected.html")
        corpus = Corpus.discover(self.root)
        self.assertEqual(coverage(corpus.select(["markdown/*"])), {"yaml": 1, "html": 0})

    def test_categories_count_top_level_directories(self) -> None:
        """pinboard/xml and pinboard/json are one parser, so one category."""
        self.write("markdown/a.input.md")
        self.write("pinboard/xml/b.input.xml")
        self.write("pinboard/json/c.input.json")
        self.assertEqual(categories(Corpus.discover(self.root).fixtures), {"markdown": 1, "pinboard": 2})

    def test_a_sidecar_does_not_leak_between_fixtures_sharing_a_prefix(self) -> None:
        self.write("markdown/a.input.md")
        self.write("markdown/a_long.input.md")
        self.write("markdown/a_long.expected.yaml")
        by_name = {f.name: f for f in Corpus.discover(self.root).fixtures}
        self.assertEqual(by_name["markdown/a"].expected, {})
        self.assertEqual(sorted(by_name["markdown/a_long"].expected), ["yaml"])


class Selection(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        for category in ("markdown", "html"):
            (self.root / category).mkdir()
            for name in ("basic", "nested"):
                (self.root / category / f"{name}.input.txt").write_text("", encoding="utf-8")
        self.corpus = Corpus.discover(self.root)

    def names(self, *patterns: str) -> list[str]:
        return [f.name for f in self.corpus.select(list(patterns))]

    def test_no_pattern_selects_everything(self) -> None:
        self.assertEqual(len(self.names()), 4)

    def test_a_bare_name_matches_as_a_substring(self) -> None:
        self.assertEqual(self.names("basic"), ["html/basic", "markdown/basic"])

    def test_a_qualified_name_selects_one(self) -> None:
        self.assertEqual(self.names("markdown/basic"), ["markdown/basic"])

    def test_a_glob_selects_a_category(self) -> None:
        self.assertEqual(self.names("markdown/*"), ["markdown/basic", "markdown/nested"])

    def test_patterns_are_a_union(self) -> None:
        self.assertEqual(self.names("markdown/basic", "html/nested"), ["html/nested", "markdown/basic"])


if __name__ == "__main__":
    unittest.main()
