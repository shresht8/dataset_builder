"""GL-3-5: `build_json_schema` must accept every value `validation.py` accepts
(§3, §7, C3). Put here, not under `shared/`, to avoid the known `tests`
package collection clash (PHASE-3-PLAN.md).
"""

from __future__ import annotations

import pytest
from groundline_api.models.dataset import DatasetColumn
from groundline_api.services.validation import row_errors
from groundline_schema import Column, ColumnType
from groundline_schema.jsonschema import build_json_schema
from jsonschema import Draft202012Validator


def _validator(columns: list[Column]) -> Draft202012Validator:
    schema = build_json_schema(columns)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _orm_column(key, type_, *, options=None, required=False) -> DatasetColumn:
    return DatasetColumn(key=key, label=key, type=type_, options=options, required=required)


def _shared_column(key, type_, *, options=None, required=False) -> Column:
    return Column(key=key, label=key, type=type_, options=options, required=required)


def test_all_six_types_map_to_expected_fragments():
    columns = [
        _shared_column("t", ColumnType.TEXT, required=True),
        _shared_column("lt", ColumnType.LONG_TEXT, required=True),
        _shared_column("sel", ColumnType.SELECT, options=["a", "b"], required=True),
        _shared_column("ms", ColumnType.MULTI_SELECT, options=["x", "y"]),
        _shared_column("num", ColumnType.NUMBER, required=True),
        _shared_column("bool", ColumnType.BOOLEAN, required=True),
    ]
    schema = build_json_schema(columns)
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"t", "lt", "sel", "num", "bool"}  # ms is optional
    assert schema["properties"]["t"] == {"type": "string", "minLength": 1}
    assert schema["properties"]["lt"] == {"type": "string", "minLength": 1}
    assert schema["properties"]["sel"] == {"type": "string", "enum": ["a", "b"]}
    assert schema["properties"]["num"] == {"type": "number"}
    assert schema["properties"]["bool"] == {"type": "boolean"}
    ms_schema = schema["properties"]["ms"]
    assert {"type": "null"} in ms_schema["anyOf"]
    array_fragment = next(f for f in ms_schema["anyOf"] if f.get("type") == "array")
    assert array_fragment["items"] == {"type": "string", "enum": ["x", "y"]}


def test_multi_select_allows_duplicates_no_unique_items():
    columns = [_shared_column("ms", ColumnType.MULTI_SELECT, options=["x", "y"])]
    validator = _validator(columns)
    assert validator.is_valid({"ms": ["x", "x", "y"]})


def test_additional_properties_rejected():
    columns = [_shared_column("t", ColumnType.TEXT, required=True)]
    validator = _validator(columns)
    assert not validator.is_valid({"t": "ok", "unknown": "nope"})


def test_sidecar_rejects_select_value_not_in_options():
    columns = [_shared_column("sel", ColumnType.SELECT, options=["a", "b"], required=True)]
    validator = _validator(columns)
    assert not validator.is_valid({"sel": "c"})


@pytest.mark.parametrize(
    "col_type,options",
    [
        (ColumnType.TEXT, None),
        (ColumnType.LONG_TEXT, None),
        (ColumnType.SELECT, ["a", "b"]),
        (ColumnType.MULTI_SELECT, ["x", "y"]),
        (ColumnType.NUMBER, None),
        (ColumnType.BOOLEAN, None),
    ],
)
@pytest.mark.parametrize("required", [True, False])
@pytest.mark.parametrize(
    "value,present",
    [
        (None, True),
        ("", True),
        ([], True),
        ("absent", False),  # sentinel: key omitted entirely
    ],
)
def test_matches_validation_for_empty_representations(col_type, options, required, value, present):
    """Every empty representation `validation.py::is_empty` allows for an
    optional column must be accepted by the sidecar too, and vice versa for
    required columns."""
    orm_col = _orm_column("k", col_type, options=options, required=required)
    shared_col = _shared_column("k", col_type, options=options, required=required)

    data = {"k": value} if present else {}
    errors = row_errors(data, [orm_col])
    validator = _validator([shared_col])
    schema_valid = validator.is_valid(data)

    assert schema_valid == (not errors), (
        f"mismatch for type={col_type} required={required} value={value!r} present={present}: "
        f"validation errors={errors}, schema_valid={schema_valid}"
    )


@pytest.mark.parametrize(
    "col_type,options,valid_value",
    [
        (ColumnType.TEXT, None, "hello"),
        (ColumnType.LONG_TEXT, None, "line one\nline two"),
        (ColumnType.SELECT, ["a", "b"], "a"),
        (ColumnType.MULTI_SELECT, ["x", "y"], ["x", "y"]),
        (ColumnType.NUMBER, None, 3.5),
        (ColumnType.BOOLEAN, None, True),
    ],
)
def test_matches_validation_for_a_valid_value_required_and_optional(col_type, options, valid_value):
    for required in (True, False):
        orm_col = _orm_column("k", col_type, options=options, required=required)
        shared_col = _shared_column("k", col_type, options=options, required=required)
        data = {"k": valid_value}
        errors = row_errors(data, [orm_col])
        validator = _validator([shared_col])
        assert not errors
        assert validator.is_valid(data)


def test_matches_validation_for_wrong_type_value():
    """A non-empty, wrong-typed value is rejected by both, required or not."""
    for required in (True, False):
        orm_col = _orm_column("k", ColumnType.NUMBER, required=required)
        shared_col = _shared_column("k", ColumnType.NUMBER, required=required)
        data = {"k": "not-a-number"}
        errors = row_errors(data, [orm_col])
        validator = _validator([shared_col])
        assert errors
        assert not validator.is_valid(data)


def test_bool_rejected_for_number_column():
    orm_col = _orm_column("k", ColumnType.NUMBER, required=True)
    shared_col = _shared_column("k", ColumnType.NUMBER, required=True)
    data = {"k": True}
    assert row_errors(data, [orm_col])
    assert not _validator([shared_col]).is_valid(data)
