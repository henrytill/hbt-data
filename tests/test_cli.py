"""Tests for the report's own plumbing.

The header is printed before any fixture runs, so anything that can make it
raise takes the whole run with it.
"""

# Each test's name is its description; a docstring would restate it.
# pylint: disable=missing-function-docstring

from __future__ import annotations

import io
import unittest

from hbt.conformance.cli import _utf8


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
