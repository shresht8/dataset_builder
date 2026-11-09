"""GL-148: row reasons in plain language, showing the bad value."""

from __future__ import annotations

from groundline_api.api.v1.infer import WEB_NO_KEY
from groundline_api.models.dataset import DatasetColumn
from groundline_api.services.validation import type_error
from groundline_schema import ColumnType
from groundline_schema.infer import NO_KEY


def _col(col_type: ColumnType, options: list[str] | None = None) -> DatasetColumn:
    return DatasetColumn(key="c", label="C", type=col_type, options=options)


def test_reasons_show_the_value_and_the_options():
    options = ["billing", "shipping"]
    assert type_error(_col(ColumnType.NUMBER), "abc") == '"abc" is not a number'
    assert type_error(_col(ColumnType.BOOLEAN), "maybe") == '"maybe" is not yes or no'
    assert type_error(_col(ColumnType.TEXT), 5) == "5 is not text"
    assert (
        type_error(_col(ColumnType.SELECT, options), "Billing")
        == '"Billing" is not an option. Options: billing, shipping'
    )
    assert (
        type_error(_col(ColumnType.MULTI_SELECT, options), ["billing", "x"])
        == '"x" is not an option. Options: billing, shipping'
    )
    assert (
        type_error(_col(ColumnType.MULTI_SELECT, options), ["x", "y"])
        == '"x", "y" are not options. Options: billing, shipping'
    )
    assert (
        type_error(_col(ColumnType.DATE), "32/13/2026")
        == '"32/13/2026" is not a date. Use YYYY-MM-DD'
    )


def test_no_python_type_names_or_brackets():
    options = ["faq", "edge"]
    reasons = [
        type_error(_col(ColumnType.NUMBER), "x"),
        type_error(_col(ColumnType.SELECT, options), "x"),
        type_error(_col(ColumnType.MULTI_SELECT, options), ["x"]),
    ]
    for reason in reasons:
        assert reason is not None
        assert "str" not in reason.split() and "[" not in reason and "'" not in reason


def test_web_infer_has_its_own_no_key_message(client, as_role):
    as_role("editor")
    resp = client.post("/v1/schema/infer", files={"file": ("data.csv", b"q\nx\n")})
    assert resp.status_code == 200, resp.text
    warnings = resp.json()["warnings"]
    assert WEB_NO_KEY in warnings
    assert NO_KEY not in warnings and "--key" not in " ".join(warnings)
