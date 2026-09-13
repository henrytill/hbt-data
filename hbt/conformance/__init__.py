"""Conformance harness for the hbt corpus.

Runs every fixture in this repository against an ``hbt`` executable and
compares what it emits, in every output format the fixture pins, against the
recorded expectation.  What "matches" means is decided in
:mod:`hbt.conformance.normalize`, which is the normative part of this package:
it is the specification the four implementations are being held to, and it is
binding on all of them by construction.

The ``normalize`` exported here is the function.  The module of that name is
reached by its dotted path, ``hbt.conformance.normalize``.
"""

__version__ = "0.1.0"

from hbt.conformance.corpus import (
    CollidingInputs,
    ContradictoryExpectations,
    Corpus,
    CorpusError,
    EmptyCorpus,
    Fixture,
    IncompleteFixture,
    MisfiledInput,
    NoCorpus,
    UnknownSidecar,
    revision,
)
from hbt.conformance.normalize import Difference, NormalizationError, compare, compare_html, normalize
from hbt.conformance.runner import Outcome, Result, Run, check, check_all, check_corpus, read_waivers
from hbt.conformance.yaml_io import load_yaml

__all__ = [
    "CollidingInputs",
    "ContradictoryExpectations",
    "Corpus",
    "CorpusError",
    "Difference",
    "EmptyCorpus",
    "Fixture",
    "IncompleteFixture",
    "MisfiledInput",
    "NoCorpus",
    "NormalizationError",
    "Outcome",
    "Result",
    "Run",
    "UnknownSidecar",
    "check",
    "check_all",
    "check_corpus",
    "compare",
    "compare_html",
    "load_yaml",
    "normalize",
    "read_waivers",
    "revision",
]
