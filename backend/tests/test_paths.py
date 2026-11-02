"""GL-3.5-13: shared dotted-path helpers (`groundline_schema.paths`).

shared/ has no test suite of its own; its tests live here (see Makefile).
"""

from __future__ import annotations

import random

import pytest
from groundline_schema.paths import PathError, flatten, nest, split_path


def test_flatten_nested_objects_to_dotted_paths():
    record = {"id": "a", "expected": {"answer": "x", "flags": {"has_text": True}}}
    assert flatten(record) == {
        "id": "a",
        "expected.answer": "x",
        "expected.flags.has_text": True,
    }


def test_arrays_scalars_and_empty_objects_are_leaves():
    record = {"calls": [{"name": "f", "args": {}}], "empty": {}, "n": 0, "none": None}
    assert flatten(record) == record


def test_literal_dotted_key_is_the_same_path_as_nesting():
    assert flatten({"expected.answer": "x"}) == flatten({"expected": {"answer": "x"}})


def test_same_path_from_two_sources_is_an_error():
    with pytest.raises(PathError, match="appears twice"):
        flatten({"a.b": 1, "a": {"b": 2}})


def test_json_leaves_are_not_recursed_into():
    record = {"meta": {"a": 1, "b.c": 2}, "x": {"y": 1}}
    assert flatten(record, leaves={"meta"}) == {"meta": {"a": 1, "b.c": 2}, "x.y": 1}


@pytest.mark.parametrize("bad", ["a..b", ".a", "a.", ""])
def test_empty_segments_are_rejected(bad):
    with pytest.raises(PathError):
        split_path(bad)
    with pytest.raises(PathError):
        flatten({bad: 1})


def test_nest_builds_intermediate_objects_only_for_present_children():
    assert nest({"id": "a", "expected.answer": "x"}) == {"id": "a", "expected": {"answer": "x"}}
    assert nest({"id": "a"}) == {"id": "a"}


def test_nest_rejects_a_value_that_is_also_a_parent():
    with pytest.raises(PathError):
        nest({"a": 1, "a.b": 2})
    with pytest.raises(PathError):
        nest({"a.b": 2, "a": 1})
    with pytest.raises(PathError):
        nest({"a": {"x": 1}, "a.b": 2})  # a json leaf object is not a parent


def _random_value(rng: random.Random, depth: int):
    kind = rng.choice(["str", "int", "bool", "none", "list", "obj", "empty_obj"])
    if kind == "str":
        return rng.choice(["", "x", "a.b", "NO", "012"])
    if kind == "int":
        return rng.randint(-3, 3)
    if kind == "bool":
        return rng.random() < 0.5
    if kind == "none":
        return None
    if kind == "list":
        return [_random_value(rng, depth + 1) for _ in range(rng.randint(0, 2))]
    if kind == "empty_obj" or depth > 2:
        return {}
    return {f"k{i}": _random_value(rng, depth + 1) for i in range(rng.randint(1, 3))}


def _random_flat_row(rng: random.Random) -> tuple[dict, set[str]]:
    """A valid flat row: column keys with no key a dotted prefix of another.

    Some columns are `json` leaves holding arbitrary values (including
    non-empty objects); the rest hold non-object values or `{}`.
    """
    row: dict = {}
    leaves: set[str] = set()
    for i in range(rng.randint(1, 5)):
        key = ".".join(f"s{i}{j}" for j in range(rng.randint(1, 3)))
        if rng.random() < 0.4:
            leaves.add(key)
            row[key] = _random_value(rng, 0)
        else:
            value = _random_value(rng, 0)
            row[key] = {} if isinstance(value, dict) else value
    return row, leaves


def test_round_trip_property_flatten_of_nest():
    rng = random.Random(3513)
    for _ in range(500):
        row, leaves = _random_flat_row(rng)
        assert flatten(nest(row), leaves) == row
