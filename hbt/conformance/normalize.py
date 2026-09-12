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
from typing import Any, Callable, Literal, Mapping, Sequence, TypeVar, cast

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
# The membership of these tuples restates collection.schema.json's property
# lists; the partition by normalization behavior is this module's own.  A test
# holds the membership to the schema, so a field added upstream shows up as one
# red test here rather than as "unknown field" against four conforming
# implementations.
ENTITY_FIELDS = frozenset(ENTITY_TEXT_LISTS + ENTITY_TIME_LISTS + ENTITY_TIMES + ENTITY_FLAGS + ("uri",))
NODE_FIELDS = frozenset({"id", "entity", "edges"})
COLLECTION_FIELDS = frozenset({"version", "length", "value"})

T = TypeVar("T")


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
    kind: Literal["value", "order", "text"] = "value"

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


def _reject_unknown(raw: Mapping[str, Any], known: frozenset[str], path: str) -> None:
    unknown = sorted(set(raw) - known)
    if unknown:
        raise NormalizationError(path, f"unknown field(s) {', '.join(unknown)}")


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


def _string(value: object, path: str) -> str:
    if not isinstance(value, str):
        raise NormalizationError(path, f"expected a string, got {value!r}")
    return value


def _set_of(value: object, path: str, item: Callable[[object, str], T]) -> list[T]:
    """A ``uniqueItems`` field.  Absent and null both read as empty."""
    entries = _require_sequence(value or [], path)
    out = [item(entry, f"{path}[{i}]") for i, entry in enumerate(entries)]
    _unique(out, path)
    return out


def _entity(value: object, path: str) -> dict[str, Any]:
    raw = _require_mapping(value, path)
    _reject_unknown(raw, ENTITY_FIELDS, path)
    for field in ENTITY_REQUIRED:
        if raw.get(field) is None:
            raise NormalizationError(f"{path}.{field}", "required field is missing")

    out: dict[str, Any] = {
        "uri": _string(raw["uri"], f"{path}.uri"),
        "createdAt": _time(raw["createdAt"], f"{path}.createdAt"),
    }
    for field in ENTITY_TIME_LISTS:
        out[field] = _set_of(raw.get(field), f"{path}.{field}", _time)
    for field in ENTITY_TEXT_LISTS:
        out[field] = _set_of(raw.get(field), f"{path}.{field}", _string)
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
    _reject_unknown(raw, NODE_FIELDS, path)
    if "id" not in raw:
        raise NormalizationError(f"{path}.id", "required field is missing")
    if "entity" not in raw:
        raise NormalizationError(f"{path}.entity", "required field is missing")
    return {
        "id": _index(raw["id"], f"{path}.id"),
        "entity": _entity(raw["entity"], f"{path}.entity"),
        "edges": _set_of(raw.get("edges"), f"{path}.edges", _index),
    }


def normalize(document: object) -> dict[str, Any]:
    """Reduce a parsed Collection to its canonical form.

    Raises :class:`NormalizationError` if the document is not a Collection.
    """
    raw = _require_mapping(document, "$")
    _reject_unknown(raw, COLLECTION_FIELDS, "$")

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


def _walk_map(path: str, expected: dict[str, object], actual: dict[str, object], out: list[Difference]) -> None:
    for key in sorted(set(expected) | set(actual)):
        if key not in expected:
            out.append(Difference(f"{path}.{key}", "(absent)", actual[key]))
        elif key not in actual:
            out.append(Difference(f"{path}.{key}", expected[key], "(absent)"))
        else:
            _walk(f"{path}.{key}", expected[key], actual[key], out)


def _walk_list(path: str, expected: list[object], actual: list[object], out: list[Difference]) -> None:
    if expected != actual and sorted(map(repr, expected)) == sorted(map(repr, actual)):
        out.append(Difference(path, expected, actual, kind="order"))
        return
    for i in range(max(len(expected), len(actual))):
        if i >= len(expected):
            out.append(Difference(f"{path}[{i}]", "(absent)", actual[i]))
        elif i >= len(actual):
            out.append(Difference(f"{path}[{i}]", expected[i], "(absent)"))
        else:
            _walk(f"{path}[{i}]", expected[i], actual[i], out)


def _walk(path: str, expected: object, actual: object, out: list[Difference]) -> None:
    if isinstance(expected, dict) and isinstance(actual, dict):
        _walk_map(path, cast("dict[str, object]", expected), cast("dict[str, object]", actual), out)
    elif isinstance(expected, list) and isinstance(actual, list):
        _walk_list(path, cast("list[object]", expected), cast("list[object]", actual), out)
    else:
        # `expected` is widened deliberately.  The isinstance tests above are
        # short-circuiting, so on this branch the checkers still hold
        # `dict[Unknown, Unknown] | list[Unknown] | object` for it -- not a
        # type anything downstream can use.  `actual` needs no such help,
        # having been tested in both arms.
        left = cast("object", expected)
        if left != actual or type(left) is not type(actual):
            out.append(Difference(path, left, actual))


def compare(expected: object, actual: object) -> list[Difference]:
    """Normalize both sides and report every way they disagree."""
    differences: list[Difference] = []
    _walk("$", normalize(expected), normalize(actual), differences)
    return differences


def _strip_terminator(document: bytes) -> bytes:
    """``document`` without its final line terminator, in either flavour."""
    return document.removesuffix(b"\r\n").removesuffix(b"\n")


def _line_endings(document: bytes) -> str:
    """How ``document`` terminates its lines, for a report that says so."""
    crlf = document.count(b"\r\n")
    lf = document.count(b"\n") - crlf
    if crlf and lf:
        return "mixed CRLF and LF line endings"
    return "CRLF line endings" if crlf else "LF line endings"


def compare_html(expected: bytes, actual: bytes) -> list[Difference]:
    """Compare rendered Netscape bookmark files.

    Byte equality, modulo a single trailing newline -- none of the leniency
    the YAML comparison grants.  There is nothing to grant: the four
    implementations render this file from the same template and produce
    identical bytes for every fixture in the corpus today, so any difference
    at all is a real divergence in a shared output format rather than a
    serializer's house style.  Widening this rule should be a decision taken
    when a divergence turns out to be legitimate, not a default.

    Bytes, not text, because that rule is otherwise not what runs: reading
    the expectation in text mode and capturing the output through a decoding
    pipe both translate CRLF to LF, so an implementation that started
    emitting DOS line endings in a shared output format would pass a
    comparison whose whole point is that the bytes match.  A difference in
    nothing but line endings is reported as that rather than as a diff, which
    would otherwise print two identical-looking lines.
    """
    left = _strip_terminator(expected)
    right = _strip_terminator(actual)
    if left == right:
        return []
    if left.replace(b"\r\n", b"\n") == right.replace(b"\r\n", b"\n"):
        return [Difference("$html", _line_endings(left), _line_endings(right))]
    diff = difflib.unified_diff(
        _lines(left),
        _lines(right),
        fromfile="expected",
        tofile="actual",
        lineterm="",
        n=1,
    )
    return [Difference("$html", None, "\n".join(diff), kind="text")]


def _lines(document: bytes) -> list[str]:
    """``document`` as diffable text, whatever it turns out to contain.

    ``errors="replace"`` for the same reason the runner decodes that way: a
    document this cannot read is a divergence to report, not a traceback.
    """
    return document.decode("utf-8", errors="replace").splitlines()
