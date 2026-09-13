"""Tests for the report's own plumbing.

The header is printed before any fixture runs, so anything that can make it
raise takes the whole run with it.
"""

# Each test's name is its description; a docstring would restate it.
# pylint: disable=missing-function-docstring

from __future__ import annotations

import io
import stat
import tempfile
import unittest
from pathlib import Path

from click.testing import CliRunner

from hbt.conformance.cli import _utf8, cli


class OutputEncoding(unittest.TestCase):
    def ascii_stream(self) -> io.TextIOWrapper:
        return io.TextIOWrapper(io.BytesIO(), encoding="ascii")

    def test_an_ascii_stream_is_reconfigured(self) -> None:
        """A LANG-less container gives stdout the ASCII codec."""
        stream = self.ascii_stream()
        with self.assertRaises(UnicodeEncodeError):
            stream.write("\u2022")
            stream.flush()
        _utf8(stream).write("\u2022 caf\u00e9")

    def test_a_utf8_stream_is_left_alone(self) -> None:
        stream = io.TextIOWrapper(io.BytesIO(), encoding="utf-8")
        self.assertIs(_utf8(stream), stream)
        self.assertEqual(stream.encoding, "utf-8")


class CommandLine(unittest.TestCase):
    """Exit codes and selection, which callers in four repositories depend on."""

    def setUp(self) -> None:
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory()))  # pylint: disable=consider-using-with
        (self.root / "markdown").mkdir()
        for name in ("basic", "nested"):
            (self.root / "markdown" / f"{name}.input.md").write_text("", encoding="utf-8")
            (self.root / "markdown" / f"{name}.expected.yaml").write_text("", encoding="utf-8")
        self.binary = self.root / "stub"
        self.binary.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        self.binary.chmod(self.binary.stat().st_mode | stat.S_IXUSR)
        self.runner = CliRunner()

    def invoke(self, *args: str) -> tuple[int, str]:
        result = self.runner.invoke(cli, ["--corpus", str(self.root), "--binary", str(self.binary), *args])
        return result.exit_code, result.output

    def test_listing_selected_fixtures_exits_zero(self) -> None:
        code, output = self.invoke("--list")
        self.assertEqual(code, 0)
        self.assertEqual(output.split(), ["markdown/basic", "markdown/nested"])

    def test_a_filter_that_matches_nothing_is_an_error(self) -> None:
        code, output = self.invoke("nope")
        self.assertEqual(code, 2)
        self.assertIn("no fixture matches nope", output)

    def test_a_failing_implementation_exits_one(self) -> None:
        code, output = self.invoke("basic")
        self.assertEqual(code, 1)
        self.assertIn("FAIL", output)

    def test_an_empty_corpus_is_a_usage_error(self) -> None:
        empty = self.root / "empty"
        (empty / "markdown").mkdir(parents=True)
        result = self.runner.invoke(cli, ["--corpus", str(empty), "--binary", str(self.binary)])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("error: no fixtures under", result.output)

    def test_a_missing_binary_is_a_usage_error(self) -> None:
        """Otherwise it is reported once per fixture, about the caller."""
        result = self.runner.invoke(cli, ["--corpus", str(self.root), "--binary", str(self.root / "gone")])
        self.assertEqual(result.exit_code, 2)
        self.assertIn("does not exist", result.output)
