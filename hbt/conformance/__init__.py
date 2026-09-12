"""Conformance harness for the hbt corpus.

Runs every fixture in this repository against an ``hbt`` executable and
compares the YAML it emits against the recorded expectation.  What "matches"
means is decided in :mod:`hbt.conformance.normalize`, which is the normative
part of this package: it is the specification the four implementations are
being held to, and it is binding on all of them by construction.
"""

__version__ = "0.1.0"

from hbt.conformance.corpus import Corpus, Fixture
from hbt.conformance.normalize import Difference, NormalizationError, compare, compare_html, normalize
from hbt.conformance.runner import Outcome, Result, check

__all__ = [
    "Corpus",
    "Difference",
    "Fixture",
    "NormalizationError",
    "Outcome",
    "Result",
    "check",
    "compare",
    "compare_html",
    "normalize",
]
