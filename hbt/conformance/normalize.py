"""What "matches" means.

This module is the specification.  Every equivalence below is a decision about
the serialized ``Collection`` -- the interoperability surface the four
implementations share -- and each one is made here once rather than four times
in four test dialects.

The contract is the YAML *data model*, not the bytes.  ``uri: 'http://x/#y'``
and ``uri: http://x/#y`` are the same document, so a difference in scalar
quoting is not a difference.  A difference in scalar *type* is: a timestamp
written as ``'1700092800'`` is a string, and a string is not an integer.

Three equivalences are granted, and nothing else is:

* **Absent, ``null``, and the empty list all mean absent.**  ``collection.schema.json``
  types ``shared``, ``toRead`` and ``isFeed`` as ``[boolean, null]`` and leaves
  them out of ``required``, so an omitted key and an explicit ``null`` are both
  the schema's way of spelling "unset".  The list-valued fields are given
  ``default: []`` for the same reason.
* **Scalar quoting does not matter**, because YAML says so.
* **Nothing else.**  In particular a quoted timestamp is a failure, not a
  formatting quirk, and a field the schema does not define is a failure rather
  than something to ignore.

The HTML formatter is held to a stricter rule -- see :func:`compare_html`.

The set-valued fields (``names``, ``labels``, ``extended``, ``updatedAt``,
``edges``) are ``uniqueItems`` in the schema, so duplicates are a shape
violation.  They are nonetheless compared *in order*: the four implementations
agree on an order today, that agreement is what makes their output
interchangeable, and losing it should be visible.  A pair that holds the same
members in a different order is reported as its own kind of difference so the
diff says which of the two problems you have.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence, cast

SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

# The Entity fields, by how each is normalized.  Names and types follow
# collection.schema.json, which is generated from hbt-rs and is the authority.
ENTITY_TEXT_LISTS = ("names", "labels", "extended")
ENTITY_TIME_LISTS = ("updatedAt",)
ENTITY_TIMES = ("createdAt", "lastVisitedAt")
ENTITY_FLAGS = ("shared", "toRead", "isFeed")
ENTITY_REQUIRED = ("uri", "createdAt")


class NormalizationError(Exception):
    """The document is not a Collection.

    Raised for shape violations -- a missing required field, a timestamp that
    is not an integer, a duplicate in a set-valued field.  This is distinct
    from a mismatch: a mismatch means two Collections disagree, whereas this
    means one of them is not a Collection at all.
    """

    def __init__(self, path: str, message: str) -> None:
        super().__init__(f"{path}: {message}")
        self.path = path
        self.message = message


@dataclass(frozen=True)
class Difference:
    """One disagreement between two normalized Collections."""

    path: str
    expected: object
    actual: object
    kind: str = "value"

    def render(self) -> str:
        """One or more lines naming where the two documents disagree."""
        if self.kind == "text":
            return f"{self.path}:\n" + "\n".join(f"  {line}" for line in str(self.actual).splitlines())
        if self.kind == "order":
            return (
                f"{self.path}: same members, different order\n"
                f"  expected {self.expected!r}\n"
                f"  actual   {self.actual!r}"
            )
        return f"{self.path}: expected {self.expected!r}, got {self.actual!r}"


def _require_mapping(value: object, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise NormalizationError(path, f"expected a mapping, got {type(value).__name__}")
    mapping = cast(Mapping[object, Any], value)
    for key in mapping:
        if not isinstance(key, str):
            raise NormalizationError(path, f"non-string key {key!r}")
    return cast(Mapping[str, Any], mapping)


def _require_sequence(value: object, path: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise NormalizationError(path, f"expected a list, got {type(value).__name__}")
    return cast(Sequence[Any], value)


def _time(value: object, path: str) -> int:
    # bool is an int in Python and is not a timestamp anywhere else.
    if isinstance(value, bool) or not isinstance(value, int):
        raise NormalizationError(path, f"expected an integer timestamp, got {value!r}")
    return value


def _flag(value: object, path: str) -> bool | None:
    if value is None:
        return None
    if not isinstance(value, bool):
        raise NormalizationError(path, f"expected a boolean or null, got {value!r}")
    return value


def _index(value: object, path: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise NormalizationError(path, f"expected a non-negative integer, got {value!r}")
    return value


def _unique(items: Sequence[Any], path: str) -> None:
    seen: list[Any] = []
    for item in items:
        if item in seen:
            raise NormalizationError(path, f"duplicate entry {item!r}")
        seen.append(item)


def _text_list(value: object, path: str) -> list[str]:
    """A set-valued field of strings.  Absent and null both read as empty."""
    if value is None:
        return []
    items = _require_sequence(value, path)
    out: list[str] = []
    for i, item in enumerate(items):
        if not isinstance(item, str):
            raise NormalizationError(f"{path}[{i}]", f"expected a string, got {item!r}")
        out.append(item)
    _unique(out, path)
    return out


def _time_list(value: object, path: str) -> list[int]:
    if value is None:
        return []
    items = _require_sequence(value, path)
    out = [_time(item, f"{path}[{i}]") for i, item in enumerate(items)]
    _unique(out, path)
    return out


def _entity(value: object, path: str) -> dict[str, Any]:
    raw = _require_mapping(value, path)
    known = set(ENTITY_TEXT_LISTS) | set(ENTITY_TIME_LISTS) | set(ENTITY_TIMES) | set(ENTITY_FLAGS) | {"uri"}
    unknown = sorted(set(raw) - known)
    if unknown:
        raise NormalizationError(path, f"unknown field(s) {', '.join(unknown)}")
    for field in ENTITY_REQUIRED:
        if raw.get(field) is None:
            raise NormalizationError(f"{path}.{field}", "required field is missing")

    uri = raw["uri"]
    if not isinstance(uri, str):
        raise NormalizationError(f"{path}.uri", f"expected a string, got {uri!r}")

    out: dict[str, Any] = {"uri": uri, "createdAt": _time(raw["createdAt"], f"{path}.createdAt")}
    for field in ENTITY_TIME_LISTS:
        out[field] = _time_list(raw.get(field), f"{path}.{field}")
    for field in ENTITY_TEXT_LISTS:
        out[field] = _text_list(raw.get(field), f"{path}.{field}")
    # Optional scalars are dropped when unset, so that absent and null land on
    # the same normalized form rather than on two forms that compare unequal.
    if raw.get("lastVisitedAt") is not None:
        out["lastVisitedAt"] = _time(raw["lastVisitedAt"], f"{path}.lastVisitedAt")
    for field in ENTITY_FLAGS:
        flag = _flag(raw.get(field), f"{path}.{field}")
        if flag is not None:
            out[field] = flag
    return out


def _node(value: object, path: str) -> dict[str, Any]:
    raw = _require_mapping(value, path)
    unknown = sorted(set(raw) - {"id", "entity", "edges"})
    if unknown:
        raise NormalizationError(path, f"unknown field(s) {', '.join(unknown)}")
    if "id" not in raw:
        raise NormalizationError(f"{path}.id", "required field is missing")
    if "entity" not in raw:
        raise NormalizationError(f"{path}.entity", "required field is missing")
    edges_raw = raw.get("edges")
    edges = [
        _index(edge, f"{path}.edges[{i}]") for i, edge in enumerate(_require_sequence(edges_raw or [], f"{path}.edges"))
    ]
    _unique(edges, f"{path}.edges")
    return {
        "id": _index(raw["id"], f"{path}.id"),
        "entity": _entity(raw["entity"], f"{path}.entity"),
        "edges": edges,
    }


def normalize(document: object) -> dict[str, Any]:
    """Reduce a parsed Collection to its canonical form.

    Raises :class:`NormalizationError` if the document is not a Collection.
    """
    raw = _require_mapping(document, "$")
    unknown = sorted(set(raw) - {"version", "length", "value"})
    if unknown:
        raise NormalizationError("$", f"unknown field(s) {', '.join(unknown)}")

    version = raw.get("version")
    if not isinstance(version, str) or not SEMVER.match(version):
        raise NormalizationError("$.version", f"expected a semver string, got {version!r}")

    nodes = [_node(n, f"$.value[{i}]") for i, n in enumerate(_require_sequence(raw.get("value") or [], "$.value"))]

    length = raw.get("length")
    if length is None:
        # length restates the node count; a serializer that omits it has not
        # said anything the value list does not already say.
        length = len(nodes)
    length = _index(length, "$.length")
    if length != len(nodes):
        raise NormalizationError("$.length", f"says {length}, but value holds {len(nodes)} node(s)")

    return {"version": version, "length": length, "value": nodes}


def _shape(value: object) -> tuple[str, object]:
    """Classify a normalized value, discarding the container's element types.

    Written as a separate step so the comparison below never handles a value
    whose element type is unknown -- ``normalize`` has already established the
    shape, and re-narrowing it inline leaves the checkers holding
    ``dict[Unknown, Unknown]``.
    """
    if isinstance(value, dict):
        return "map", cast("dict[str, object]", value)
    if isinstance(value, list):
        return "list", cast("list[object]", value)
    return "scalar", value


def _walk(path: str, expected: object, actual: object, out: list[Difference]) -> None:
    expected_kind, expected_value = _shape(expected)
    actual_kind, actual_value = _shape(actual)

    if expected_kind == "map" and actual_kind == "map":
        expected_map = cast("dict[str, object]", expected_value)
        actual_map = cast("dict[str, object]", actual_value)
        for key in sorted(set(expected_map) | set(actual_map)):
            if key not in expected_map:
                out.append(Difference(f"{path}.{key}", "(absent)", actual_map[key]))
            elif key not in actual_map:
                out.append(Difference(f"{path}.{key}", expected_map[key], "(absent)"))
            else:
                _walk(f"{path}.{key}", expected_map[key], actual_map[key], out)
        return

    if expected_kind == "list" and actual_kind == "list":
        expected_list = cast("list[object]", expected_value)
        actual_list = cast("list[object]", actual_value)
        if expected_list != actual_list and sorted(map(repr, expected_list)) == sorted(map(repr, actual_list)):
            out.append(Difference(path, expected_list, actual_list, kind="order"))
            return
        for i in range(max(len(expected_list), len(actual_list))):
            if i >= len(expected_list):
                out.append(Difference(f"{path}[{i}]", "(absent)", actual_list[i]))
            elif i >= len(actual_list):
                out.append(Difference(f"{path}[{i}]", expected_list[i], "(absent)"))
            else:
                _walk(f"{path}[{i}]", expected_list[i], actual_list[i], out)
        return

    if expected_value != actual_value or type(expected_value) is not type(actual_value):
        out.append(Difference(path, expected_value, actual_value))


def compare(expected: object, actual: object) -> list[Difference]:
    """Normalize both sides and report every way they disagree."""
    differences: list[Difference] = []
    _walk("$", normalize(expected), normalize(actual), differences)
    return differences


def compare_html(expected: str, actual: str) -> list[Difference]:
    """Compare rendered Netscape bookmark files.

    Byte equality, modulo a single trailing newline -- none of the leniency
    the YAML comparison grants.  There is nothing to grant: the four
    implementations render this file from the same template and produce
    identical bytes for every fixture in the corpus today, so any difference
    at all is a real divergence in a shared output format rather than a
    serializer's house style.  Widening this rule should be a decision taken
    when a divergence turns out to be legitimate, not a default.
    """
    left = expected[:-1] if expected.endswith("\n") else expected
    right = actual[:-1] if actual.endswith("\n") else actual
    if left == right:
        return []
    diff = difflib.unified_diff(
        left.splitlines(),
        right.splitlines(),
        fromfile="expected",
        tofile="actual",
        lineterm="",
        n=1,
    )
    return [Difference("$html", left, "\n".join(diff), kind="text")]
