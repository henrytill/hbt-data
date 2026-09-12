"""Tests for the equivalences the normalizer grants, and the ones it refuses.

These are tests of the specification, not of the corpus.  A change here is a
change to what conformance means for all four implementations.
"""

# Each test's name is its description; a docstring would restate it.
# pylint: disable=missing-function-docstring

from __future__ import annotations

import unittest
from typing import Any

from hbt.conformance.normalize import NormalizationError, compare, compare_html, normalize
from hbt.conformance.yaml_io import load_yaml


def collection(*entities: dict[str, Any]) -> dict[str, Any]:
    value = [{"id": i, "entity": e, "edges": []} for i, e in enumerate(entities)]
    return {"version": "0.1.0", "length": len(value), "value": value}


def entity(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "uri": "https://example.com/",
        "createdAt": 1700092800,
        "updatedAt": [],
        "names": [],
        "labels": [],
    }
    base.update(overrides)
    return base


class GrantedEquivalences(unittest.TestCase):
    def test_scalar_quoting_is_not_a_difference(self) -> None:
        """The contract is the YAML data model, not the bytes.

        Live on three corpus fixtures: hbt-rs and hbt-ocaml leave a URI
        containing '#' unquoted where hbt-go and hbt-hs quote it.
        """
        plain = "version: 0.1.0\nlength: 1\nvalue:\n- id: 0\n  entity:\n    uri: https://x/y#z\n"
        quoted = plain.replace("https://x/y#z", "'https://x/y#z'")
        body = "    createdAt: 1700092800\n    updatedAt: []\n    names: []\n    labels: []\n  edges: []\n"
        self.assertEqual(compare(load_yaml(plain + body), load_yaml(quoted + body)), [])

    def test_null_equals_absent_for_optional_flags(self) -> None:
        explicit = collection(entity(shared=None, toRead=None, isFeed=None))
        self.assertEqual(compare(explicit, collection(entity())), [])

    def test_empty_list_equals_absent(self) -> None:
        self.assertEqual(compare(collection(entity(extended=[])), collection(entity())), [])

    def test_absent_length_is_taken_from_the_value_list(self) -> None:
        doc = {"version": "0.1.0", "value": [{"id": 0, "entity": entity(), "edges": []}]}
        self.assertEqual(normalize(doc)["length"], 1)

    def test_absent_edges_read_as_empty(self) -> None:
        doc = {"version": "0.1.0", "length": 1, "value": [{"id": 0, "entity": entity()}]}
        self.assertEqual(normalize(doc)["value"][0]["edges"], [])


class Refusals(unittest.TestCase):
    def test_a_quoted_timestamp_is_a_failure(self) -> None:
        """Not a formatting quirk: a string is not an integer.

        This is the shape of a YAML emitter regression -- the sort of change
        every implementation's own suite passes, because they all compare
        decoded structures rather than the serialized form.
        """
        with self.assertRaisesRegex(NormalizationError, "createdAt"):
            normalize(collection(entity(createdAt="1700092800")))

    def test_a_boolean_timestamp_is_a_failure(self) -> None:
        with self.assertRaisesRegex(NormalizationError, "createdAt"):
            normalize(collection(entity(createdAt=True)))

    def test_unknown_fields_are_a_failure(self) -> None:
        with self.assertRaisesRegex(NormalizationError, "unknown field"):
            normalize(collection(entity(favourite=True)))

    def test_missing_uri_is_a_failure(self) -> None:
        body = entity()
        del body["uri"]
        with self.assertRaisesRegex(NormalizationError, "uri"):
            normalize(collection(body))

    def test_duplicates_in_a_set_valued_field_are_a_failure(self) -> None:
        with self.assertRaisesRegex(NormalizationError, "duplicate"):
            normalize(collection(entity(labels=["a", "a"])))

    def test_length_disagreeing_with_the_node_count_is_a_failure(self) -> None:
        doc = collection(entity())
        doc["length"] = 7
        with self.assertRaisesRegex(NormalizationError, "length"):
            normalize(doc)

    def test_an_empty_string_is_not_an_empty_list(self) -> None:
        """Only null reads as absent.  Every other non-sequence is a failure.

        A serializer emitting ``names: ''`` for an entity with no names has
        changed the serialized form, which is exactly what this harness exists
        to catch.
        """
        empties: list[Any] = ["", {}, 0, False]
        for empty in empties:
            with self.subTest(empty=empty):
                with self.assertRaisesRegex(NormalizationError, "names"):
                    normalize(collection(entity(names=empty)))

    def test_an_empty_string_is_not_an_empty_value_list(self) -> None:
        empties: list[Any] = ["", {}, 0, False]
        for empty in empties:
            with self.subTest(empty=empty):
                doc = {"version": "0.1.0", "length": 0, "value": empty}
                with self.assertRaisesRegex(NormalizationError, r"\$\.value"):
                    normalize(doc)

    def test_a_bad_version_is_a_failure(self) -> None:
        doc = collection(entity())
        doc["version"] = "one"
        with self.assertRaisesRegex(NormalizationError, "version"):
            normalize(doc)


class HtmlComparison(unittest.TestCase):
    """The rendered bookmark file is held to byte equality, not to a data model."""

    DOC = b"<!DOCTYPE NETSCAPE-Bookmark-file-1>\n<DL><p>\n</DL><p>\n"

    def test_identical_documents_match(self) -> None:
        self.assertEqual(compare_html(self.DOC, self.DOC), [])

    def test_a_trailing_newline_is_not_a_difference(self) -> None:
        self.assertEqual(compare_html(self.DOC, self.DOC.rstrip(b"\n")), [])

    def test_whitespace_is_a_difference(self) -> None:
        self.assertEqual(len(compare_html(self.DOC, self.DOC.replace(b"<DL><p>", b"  <DL><p>"))), 1)

    def test_the_difference_is_reported_as_a_diff(self) -> None:
        (difference,) = compare_html(self.DOC, self.DOC.replace(b"<DL><p>", b"<DL>"))
        rendered = difference.render()
        self.assertIn("--- expected", rendered)
        self.assertIn("+++ actual", rendered)

    def test_line_endings_are_a_difference_and_are_named_as_one(self) -> None:
        """Text mode would have translated both sides into agreement."""
        (difference,) = compare_html(self.DOC, self.DOC.replace(b"\n", b"\r\n"))
        self.assertEqual(difference.expected, "LF line endings")
        self.assertEqual(difference.actual, "CRLF line endings")


class Reporting(unittest.TestCase):
    def test_reordering_a_set_valued_field_is_reported_as_an_order_difference(self) -> None:
        differences = compare(collection(entity(labels=["a", "b"])), collection(entity(labels=["b", "a"])))
        self.assertEqual(len(differences), 1)
        difference = differences[0]
        self.assertEqual(difference.kind, "order")
        self.assertIn("different order", difference.render())

    def test_a_changed_member_is_reported_as_a_value_difference(self) -> None:
        differences = compare(collection(entity(labels=["a", "b"])), collection(entity(labels=["a", "c"])))
        self.assertEqual(len(differences), 1)
        difference = differences[0]
        self.assertEqual(difference.kind, "value")
        self.assertEqual(difference.path, "$.value[0].entity.labels[1]")

    def test_an_optional_flag_appearing_is_reported_against_absence(self) -> None:
        differences = compare(collection(entity()), collection(entity(isFeed=True)))
        self.assertEqual(len(differences), 1)
        difference = differences[0]
        self.assertEqual(difference.path, "$.value[0].entity.isFeed")
        self.assertEqual(difference.expected, "(absent)")
        self.assertIs(difference.actual, True)

    def test_every_disagreement_is_reported_not_just_the_first(self) -> None:
        expected = collection(entity(), entity(uri="https://a/"))
        actual = collection(entity(isFeed=True), entity(uri="https://b/"))
        self.assertEqual(len(compare(expected, actual)), 2)


if __name__ == "__main__":
    unittest.main()
