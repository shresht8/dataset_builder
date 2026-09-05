"""Placeholder so the suite runs before real tests exist."""


def test_column_types_are_six():
    from groundline_schema import ColumnType

    assert len(list(ColumnType)) == 6
