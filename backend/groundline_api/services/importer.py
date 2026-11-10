"""CSV / XLSX / JSON / JSONL / YAML import: parsing, mapping, validation (§4, §8).

Two-step flow used by `api/v1/imports.py`:
  - `preview()` -- a single pass over the file that returns a sample of the
    first rows, the total row count, and (if a mapping is supplied) the full
    per-row validation report. Persists nothing.
  - `commit()` -- a single pass that applies the mapping plus caller-supplied
    skip/fix overrides and returns, per source row, whether it imported,
    was explicitly skipped, or errored. The route creates a `DatasetRow` for
    each row `commit()` returns as valid.

CSV/XLSX are streamed (the CSV reader / openpyxl `iter_rows` generator,
consumed once). JSON/JSONL/YAML are parsed whole by the shared
`groundline_schema.records` (GL-3.5-5), and each record is flattened to dotted
source columns, with the dataset's `json` column keys kept as leaves.

Row index convention: 1-based, counting only rows of data (the header row of
a CSV/XLSX is not numbered; for JSON/JSONL/YAML it's the record's position).

Coercion from spreadsheet strings, applied per mapped column before
validation (GL-2-8):
  - number:        "4" / "4.5" -> int / float; anything else that doesn't
                    parse is left as the raw string, so validation reports
                    "expected a number".
  - boolean:        true/false/1/0/yes/no (case-insensitive, trimmed) -> bool;
                    anything else is left as the raw string.
  - multi_select:   split on comma or semicolon, each part trimmed, empty
                    parts dropped.
  - select:         trimmed string (still option-checked downstream).
  - text/long_text: the raw string, unmodified.
  - json:           the raw string, as a JSON string.
An empty cell is treated as an absent value (the column's `required` check
applies to it; it is never coerced).

Dates (GL-3.5-9), every format: a `YYYY-MM-DD` string is kept; a string or
XLSX cell with a time component is refused ("has a time component") unless
it's an XLSX datetime at exactly midnight; XLSX time/duration cells and
numbers are refused (a serial or epoch number is never guessed); any other
string is left for validation to report. XLSX date cells stay native values
(read with the workbook's own 1900/1904 epoch) instead of their str().

Typed values from JSON/YAML (GL-3.5-5): strings -- and plain YAML scalars,
by their raw text -- take the string rules above, except that a plain YAML
scalar going to a `json` column is typed the way JSON would type it. Numbers
and booleans are kept where the column type allows (number -> text is its
string; 0/1 -> boolean; booleans -> "true"/"false" text); arrays fit only
multi_select or json, objects only json. null means absent.

If the dataset has a key column, a key repeated within the file or already
used in the dataset is a row error; import never updates rows (that's sync).
"""

from __future__ import annotations

import csv
import datetime as dt
import io
import itertools
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

import openpyxl
from groundline_schema import ColumnType
from groundline_schema.paths import PathError, flatten
from groundline_schema.records import (
    STRUCTURED_SUFFIXES,
    PlainScalar,
    RecordProblem,
    RecordsError,
    decode_text,
    parse_records,
    to_json_value,
)
from groundline_schema.temporal import is_date
from openpyxl.utils.exceptions import InvalidFileException

from groundline_api.config import settings
from groundline_api.models.dataset import DatasetColumn
from groundline_api.services.validation import key_column, row_errors, show_value

SAMPLE_SIZE = 20
# CSV delimiters tried by `detect_delimiter`, in order of preference on a tie.
CSV_DELIMITERS = (",", ";", "\t", "|")
_DELIMITER_SAMPLE_RECORDS = 50
# The preview lists at most this many rows with errors; the totals are exact.
ERROR_ROWS_SHOWN = 200
NO_MAPPED_VALUES = "no values in the mapped columns"
# validation.row_errors reports an empty required value as "required" (GL-149).
MISSING_VALUE = "empty, but this column is required. Fill it in or skip this row"


class ImportRequestError(Exception):
    """A caller-facing (422) error in the uploaded file or the supplied mapping."""

    def __init__(self, message: str, candidates: list[str] | None = None) -> None:
        super().__init__(message)
        self.candidates = candidates  # records_key choices, when one is needed


class _CoercionError(Exception):
    """A value that can't go into its mapped column at all (a row error)."""


@dataclass
class ParsedSource:
    header: list[str]
    # Per data row: {source column: value}, or a RecordProblem for a record
    # that couldn't be read. CSV/XLSX values are raw cell strings.
    rows: Iterator[dict[str, Any] | RecordProblem]
    structured: bool = False
    records_key: str | None = None
    ignored_keys: list[str] = field(default_factory=list)
    delimiter: str | None = None  # CSV only: the one used
    # CSV/XLSX: indices of rows with non-empty cells past the header, filled
    # while `rows` is iterated (GL-146).
    extra_cell_rows: list[int] = field(default_factory=list)


def parse_upload(
    filename: str,
    content: bytes,
    columns: list[DatasetColumn] | None = None,
    records_key: str | None = None,
    delimiter: str | None = None,
) -> ParsedSource:
    """Parse an upload into source columns and a row iterator.

    A CSV uses `delimiter` when given (one of CSV_DELIMITERS), else the detected one.
    """
    lower = filename.lower()
    if lower.endswith((".csv", ".xlsx")):
        source = _parse_csv(content, delimiter) if lower.endswith(".csv") else _parse_xlsx(content)
        problem = header_problem(source.header)
        if problem:
            raise ImportRequestError(problem)
        return source
    if lower.endswith(STRUCTURED_SUFFIXES):
        return _parse_structured(filename, content, columns or [], records_key)
    raise ImportRequestError(
        f"unsupported file type: {filename!r} "
        "(expected .csv, .xlsx, .json, .jsonl, .ndjson, .yaml or .yml)"
    )


EMPTY_FILE = "this file is empty: it has no header row and no data. Choose another file"
TITLE_ROWS_HINT = "If the file starts with title rows above the header, delete them"


def unnamed_column(position: int) -> str:
    return (
        f"column {position} has no name. Name it in the file's first row, or remove the"
        f" column. {TITLE_ROWS_HINT}"
    )


def header_problem(header: list[str]) -> str | None:
    """CSV/XLSX header problems that would lose data (GL-147), or None.

    An empty file; a nameless cell before the last named one; a repeated name
    (each row is read into a dict, so the later column would replace the earlier
    one). Nameless cells after the last name are left to the caller.
    """
    named = [i for i, name in enumerate(header) if name.strip()]
    if not named:
        return EMPTY_FILE
    problems: list[str] = []
    gaps = [i + 1 for i in range(named[-1]) if not header[i].strip()]
    if gaps:
        problems.append(unnamed_column(gaps[0]))
    seen: set[str] = set()
    repeated: list[str] = []
    for name in header:
        if name.strip() and name in seen and name not in repeated:
            repeated.append(name)
        seen.add(name)
    if repeated:
        names = ", ".join(f'"{name}"' for name in repeated)
        problems.append(
            f"the column name {names} is used more than once."
            " Give each column its own name in the file's first row"
        )
    return "; ".join(problems) if problems else None


def detect_delimiter(text: str) -> str:
    """The CSV_DELIMITERS entry that splits the first records most consistently.

    A candidate must split the header into at least two columns. Among those, the
    one with the most non-blank records as wide as the header wins, then the
    widest header, then the earlier entry. With no candidate, a comma.
    """
    best, best_score = ",", (0, 0)
    for delimiter in CSV_DELIMITERS:
        try:
            records = list(
                itertools.islice(
                    csv.reader(io.StringIO(text), delimiter=delimiter), _DELIMITER_SAMPLE_RECORDS
                )
            )
        except csv.Error:
            continue  # e.g. one huge field: this isn't the delimiter
        widths = [len(r) for r in records if any(cell.strip() for cell in r)]
        if not widths or widths[0] < 2:
            continue
        score = (sum(1 for w in widths if w == widths[0]), widths[0])
        if score > best_score:
            best, best_score = delimiter, score
    return best


def _parse_csv(content: bytes, delimiter: str | None = None) -> ParsedSource:
    try:
        text = decode_text(content, csv=True)
    except RecordsError as exc:
        raise ImportRequestError(str(exc)) from None
    if delimiter is None:
        delimiter = detect_delimiter(text)
    elif delimiter not in CSV_DELIMITERS:
        raise ImportRequestError("delimiter: expected a comma, semicolon, tab or |")
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    try:
        header = next(reader)
    except StopIteration:
        header = []

    source = ParsedSource(header=header, rows=iter(()), delimiter=delimiter)

    def rows() -> Iterator[dict[str, Any]]:
        for index, raw_row in enumerate(reader, start=1):
            _note_extra_cells(source, index, raw_row, len(header))
            yield _cells(header, raw_row)

    source.rows = rows()
    return source


def _parse_xlsx(content: bytes) -> ParsedSource:
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        row_iter = workbook.worksheets[0].iter_rows(values_only=True)
        first = next(row_iter, None)
    except (zipfile.BadZipFile, InvalidFileException, KeyError, OSError, ValueError, IndexError):
        raise ImportRequestError(
            "could not read the workbook — is it a valid, unencrypted .xlsx file?"
        ) from None
    header = [] if first is None else ["" if cell is None else str(cell) for cell in first]
    # Sheet rows are as wide as the sheet, so cells past the header sit under
    # blank header cells: the header ends at its last named cell.
    width = max((i + 1 for i, name in enumerate(header) if name.strip()), default=0)
    source = ParsedSource(header=header, rows=iter(()))

    def rows() -> Iterator[dict[str, Any]]:
        for index, raw_row in enumerate(row_iter, start=1):
            cells = [_xlsx_cell(cell) for cell in raw_row]
            _note_extra_cells(source, index, cells, width)
            yield _cells(header, cells)

    source.rows = rows()
    return source


def _xlsx_cell(cell: Any) -> Any:
    # Date/time cells stay native so date columns can tell a date from a
    # datetime; every other column still sees their str(), as before.
    if cell is None:
        return ""
    if isinstance(cell, (dt.date, dt.time, dt.timedelta)):
        return cell
    return str(cell)


def _note_extra_cells(source: ParsedSource, index: int, raw_row: list[Any], width: int) -> None:
    if any(_has_value(cell) for cell in raw_row[width:]):
        source.extra_cell_rows.append(index)


def _cells(header: list[str], raw_row: list[Any]) -> dict[str, Any]:
    return {header[i]: (raw_row[i] if i < len(raw_row) else "") for i in range(len(header))}


def _parse_structured(
    filename: str, content: bytes, columns: list[DatasetColumn], records_key: str | None
) -> ParsedSource:
    try:
        parsed = parse_records(filename, content, records_key, settings.import_max_nodes)
    except RecordsError as exc:
        raise ImportRequestError(str(exc), exc.candidates) from None

    leaves = {col.key for col in columns if col.type == ColumnType.JSON}
    header: list[str] = []
    seen: set[str] = set()
    rows: list[dict[str, Any] | RecordProblem] = []
    for record in parsed.records:
        if isinstance(record, RecordProblem):
            rows.append(record)
            continue
        try:
            flat = flatten(record, leaves)
        except PathError as exc:
            rows.append(RecordProblem(str(exc)))
            continue
        for path in flat:
            if path not in seen:
                seen.add(path)
                header.append(path)
        rows.append(flat)

    return ParsedSource(
        header=header,
        rows=iter(rows),
        structured=True,
        records_key=parsed.records_key,
        ignored_keys=parsed.ignored_keys,
    )


def _has_value(value: Any) -> bool:
    if value is None:
        return False
    return not (isinstance(value, str) and value.strip() == "")


def is_blank_line(source: ParsedSource, values: dict[str, Any] | RecordProblem) -> bool:
    """A CSV/XLSX row with every cell empty. A `{}` record in JSON/YAML is a real record."""
    if source.structured or isinstance(values, RecordProblem):
        return False
    return not any(_has_value(v) for v in values.values())


def compatibility(
    header: list[str], columns: list[DatasetColumn], mapping: dict[str, str]
) -> dict[str, list[str]]:
    """What the mapping does with the file's columns and the schema's columns (GL-133)."""
    active = [c for c in columns if not c.archived]
    mapped_keys = set(mapping.values())
    unfilled = [c for c in active if c.key not in mapped_keys]
    return {
        "matched": [h for h in header if h in mapping],
        "ignored_source_columns": [h for h in header if h not in mapping],
        "unfilled_schema_columns": [c.key for c in unfilled],
        "required_without_source": [c.key for c in unfilled if c.required],
    }


def _iterate_rows(source: ParsedSource) -> Iterator[tuple[int, dict[str, Any] | RecordProblem]]:
    """Yield (1-based row index, row values or a RecordProblem) for each data row."""
    yield from enumerate(source.rows, start=1)


def suggest_mapping(header: list[str], columns: list[DatasetColumn]) -> dict[str, str]:
    """Case-insensitive header -> schema key matches; first match wins per key."""
    keys_by_lower = {col.key.lower(): col.key for col in columns if not col.archived}
    mapping: dict[str, str] = {}
    used_keys: set[str] = set()
    for source_col in header:
        key = keys_by_lower.get(source_col.strip().lower())
        if key and key not in used_keys:
            mapping[source_col] = key
            used_keys.add(key)
    return mapping


def validate_mapping(mapping: dict[str, str], columns: list[DatasetColumn]) -> None:
    """Raise ImportRequestError for a mapping that references an unknown or duplicate key."""
    valid_keys = {col.key for col in columns}
    seen_keys: dict[str, str] = {}
    for source_col, schema_key in mapping.items():
        if schema_key not in valid_keys:
            raise ImportRequestError(f"mapping: '{schema_key}' is not a schema column")
        if schema_key in seen_keys:
            raise ImportRequestError(
                f"mapping: '{seen_keys[schema_key]}' and '{source_col}' "
                f"both map to '{schema_key}'"
            )
        seen_keys[schema_key] = source_col


def _coerce(raw: str, col_type: ColumnType) -> Any:
    if col_type == ColumnType.SELECT:
        return raw.strip()
    if col_type == ColumnType.MULTI_SELECT:
        return [part.strip() for part in re.split(r"[,;]", raw) if part.strip()]
    if col_type == ColumnType.NUMBER:
        try:
            return int(raw)
        except ValueError:
            try:
                return float(raw)
            except ValueError:
                return raw
    if col_type == ColumnType.BOOLEAN:
        lowered = raw.strip().lower()
        if lowered in ("true", "1", "yes"):
            return True
        if lowered in ("false", "0", "no"):
            return False
        return raw
    return raw  # text / long_text / json: as-is


_HAS_TIME = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d")


def _coerce_date(value: Any) -> Any:
    """Coerce one value for a date column (see the module docstring)."""
    if isinstance(value, dt.datetime):
        if value.time() != dt.time(0):
            raise _CoercionError(f"{show_value(value.isoformat(sep=' '))} has a time. Use YYYY-MM-DD")
        return value.date().isoformat()
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, (dt.time, dt.timedelta)):
        raise _CoercionError("a time is not a date. Use YYYY-MM-DD")
    if isinstance(value, (bool, int, float)):
        raise _CoercionError(f"{show_value(value)} is a number, not a date. Use YYYY-MM-DD")
    if isinstance(value, str):
        text = value.strip()
        if is_date(text):
            return text
        if _HAS_TIME.match(text):
            raise _CoercionError(f"{show_value(text)} has a time. Use YYYY-MM-DD")
        return str(value)  # validation names the expected YYYY-MM-DD form
    raise _CoercionError("a list or object can only go in a json column")


def _coerce_typed(value: Any, col_type: ColumnType) -> Any:
    """Coerce one JSON/YAML value for its column (see the module docstring)."""
    if col_type == ColumnType.DATE:
        return _coerce_date(value)
    if col_type == ColumnType.JSON:
        try:
            return to_json_value(value)
        except ValueError as exc:
            raise _CoercionError(str(exc)) from None
    if isinstance(value, str):  # including plain YAML scalars, by their raw text
        return _coerce(str(value), col_type)
    if isinstance(value, bool):
        if col_type in (ColumnType.TEXT, ColumnType.LONG_TEXT, ColumnType.SELECT):
            return "true" if value else "false"
        return value
    if isinstance(value, (int, float)):
        if col_type in (ColumnType.TEXT, ColumnType.LONG_TEXT, ColumnType.SELECT):
            return str(value)
        if col_type == ColumnType.BOOLEAN and value in (0, 1):
            return bool(value)
        return value
    if isinstance(value, list) and col_type == ColumnType.MULTI_SELECT:
        return [str(item) if isinstance(item, PlainScalar) else item for item in value]
    raise _CoercionError("a list or object can only go in a json column")


def map_row(
    raw_values: dict[str, Any],
    mapping: dict[str, str],
    columns_by_key: dict[str, DatasetColumn],
    structured: bool,
) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    """Apply the mapping + type coercion to one source row.

    Returns (data, coercion failures as (column_key, reason)).
    """
    data: dict[str, Any] = {}
    failures: list[tuple[str, str]] = []
    for source_col, schema_key in mapping.items():
        raw = raw_values.get(source_col)
        col_type = columns_by_key[schema_key].type
        if not structured:
            if raw is None or (isinstance(raw, str) and raw.strip() == ""):
                continue  # empty cell = absent
            try:
                if col_type == ColumnType.DATE:
                    data[schema_key] = _coerce_date(raw)
                else:  # an XLSX date cell outside a date column: its str(), as before
                    data[schema_key] = _coerce(raw if isinstance(raw, str) else str(raw), col_type)
            except _CoercionError as exc:
                failures.append((schema_key, str(exc)))
            continue
        if raw is None:
            continue  # null / missing = absent
        try:
            data[schema_key] = _coerce_typed(raw, col_type)
        except _CoercionError as exc:
            failures.append((schema_key, str(exc)))
    return data, failures


def _sample_values(values: dict[str, Any] | RecordProblem) -> dict[str, Any]:
    """A row's source values for the preview sample, typed as JSON would show them."""
    if isinstance(values, RecordProblem):
        return {}
    shown: dict[str, Any] = {}
    for column, value in values.items():
        try:
            shown[column] = to_json_value(value)
        except ValueError:
            shown[column] = str(value)
    return shown


@dataclass
class _KeyCheck:
    """Key column rules for one pass: unique within the file, new to the dataset."""

    column: DatasetColumn | None
    existing: set[str]
    first_row: dict[str, int] = field(default_factory=dict)
    # What the last `failures` call found: None, "repeated" or "existing" (GL-142).
    last: str | None = None

    def failures(self, index: int, data: dict[str, Any]) -> list[tuple[str, str]]:
        self.last = None
        if self.column is None:
            return []
        value = data.get(self.column.key)
        if not isinstance(value, str) or value.strip() == "":
            return []  # row_errors already reports a missing/invalid key
        key = self.column.key
        if value in self.first_row:
            self.last = "repeated"
            reason = (
                f"{show_value(value)} is also the key of row {self.first_row[value]}."
                " Keys must be unique, so skip one of the two rows"
            )
            return [(key, reason)]
        self.first_row[value] = index
        if value in self.existing:
            self.last = "existing"
            reason = (
                f"{show_value(value)} is already in this dataset."
                " Import only adds new rows, so skip this row"
            )
            return [(key, reason)]
        return []


ID_COLUMN = "id"
WARNING_EXAMPLES = 5


@dataclass
class _IdCheck:
    """Without a key column, a mapped source `id` that repeats or is empty (GL-146).

    A warning, not a row error: those rows still import.
    """

    seen: set[str] = field(default_factory=set)
    repeated: list[str] = field(default_factory=list)
    repeated_rows: int = 0
    missing_rows: int = 0

    def add(self, values: dict[str, Any]) -> None:
        value = values.get(ID_COLUMN)
        if not _has_value(value):
            self.missing_rows += 1
            return
        text = str(value)
        if text in self.seen:
            self.repeated_rows += 1
            if text not in self.repeated:
                self.repeated.append(text)
        self.seen.add(text)

    def report(self) -> dict[str, Any] | None:
        if self.repeated_rows == 0 and self.missing_rows == 0:
            return None
        return {
            "column": ID_COLUMN,
            "repeated": self.repeated[:WARNING_EXAMPLES],
            "repeated_rows": self.repeated_rows,
            "missing_rows": self.missing_rows,
        }


def _validate_row(
    index: int,
    values: dict[str, Any] | RecordProblem,
    mapping: dict[str, str],
    columns: list[DatasetColumn],
    columns_by_key: dict[str, DatasetColumn],
    structured: bool,
    fixes: dict[str, Any],
    keys: _KeyCheck,
) -> tuple[dict[str, Any], list[tuple[str, str]]]:
    if isinstance(values, RecordProblem):
        return {}, [("", values.reason)]
    data, failures = map_row(values, mapping, columns_by_key, structured)
    had_values = any(_has_value(v) for v in values.values())
    for key, value in fixes.items():
        # A fix replaces the source value, so its coercion failure no longer applies;
        # date fixes take the same date rules as the source.
        failures = [(k, reason) for k, reason in failures if k != key]
        column = columns_by_key.get(key)
        try:
            data[key] = _coerce_date(value) if column and column.type == ColumnType.DATE and value is not None else value
        except _CoercionError as exc:
            failures.append((key, str(exc)))
    if not data and not failures and had_values:
        return data, [("", NO_MAPPED_VALUES)]
    failed = {column for column, _ in failures}
    failures += [
        (k, MISSING_VALUE if reason == "required" else reason)
        for k, reason in row_errors(data, columns)
        if k not in failed
    ]
    failures += keys.failures(index, data)
    return data, failures


def preview(
    source: ParsedSource,
    columns: list[DatasetColumn],
    mapping: dict[str, str] | None,
    existing_keys: set[str] | None = None,
) -> tuple[list[dict[str, Any]], int, int, dict[str, Any] | None]:
    """Single pass: sample rows, total count, blank lines and an optional validation report.

    Returns (sample_rows, total_rows, blank_skipped, validation). sample_rows is a
    list of `{"row": index, "values": {...}}` (up to SAMPLE_SIZE, blank lines left
    out). total_rows counts blank CSV/XLSX lines too, so indices stay positions in
    the file. validation is `None` if no mapping was supplied, else the report:
    `valid`, at most ERROR_ROWS_SHOWN rows' worth of `errors`, and exact totals
    (`error_total`, `failed_rows`, `checked_rows`, `by_column`, and the failed rows
    whose only problem is the key: `existing_key_rows`, `repeated_key_rows`), plus the warnings
    `extra_cells` and `id_check` (None when there is nothing to warn about).
    """
    columns_by_key = {col.key: col for col in columns}
    keys = _KeyCheck(key_column(columns), existing_keys or set())
    id_check = (
        _IdCheck()
        if mapping is not None and keys.column is None and ID_COLUMN in mapping
        else None
    )
    sample: list[dict[str, Any]] = []
    total = 0
    blank = 0
    valid = 0
    failed_rows = 0
    error_total = 0
    shown_rows = 0
    errors: list[dict[str, Any]] = []
    by_column: dict[tuple[str, str], dict[str, Any]] = {}
    # Rows whose only failure is the key: already in the dataset, or repeated in the file.
    key_only = {"existing": 0, "repeated": 0}

    for index, values in _iterate_rows(source):
        total += 1
        if is_blank_line(source, values):
            blank += 1
            continue
        if len(sample) < SAMPLE_SIZE:
            sample.append({"row": index, "values": _sample_values(values)})
        if mapping is None:
            continue
        if id_check is not None and not isinstance(values, RecordProblem):
            id_check.add(values)
        keys.last = None  # _validate_row may return before checking the key
        _, failures = _validate_row(
            index, values, mapping, columns, columns_by_key, source.structured, {}, keys
        )
        if not failures:
            valid += 1
            continue
        failed_rows += 1
        if keys.last is not None and len(failures) == 1:
            key_only[keys.last] += 1
        error_total += len(failures)
        for i, (key, reason) in enumerate(failures):
            # One summary line per kind of problem in a column (GL-149): the key check
            # comes last in `failures`; an empty value has a fixed reason.
            if keys.last is not None and i == len(failures) - 1:
                kind = "key"
            elif reason == MISSING_VALUE:
                kind = "missing"
            else:
                kind = "value"
            entry = by_column.setdefault(
                (key, kind), {"column": key, "rows": 0, "reason": reason, "mixed": False}
            )
            entry["rows"] += 1
            entry["mixed"] = entry["mixed"] or reason != entry["reason"]
        if shown_rows < ERROR_ROWS_SHOWN:
            shown_rows += 1
            errors.extend(
                {"row": index, "column": key, "reason": reason} for key, reason in failures
            )

    validation = None
    if mapping is not None:
        validation = {
            "valid": valid,
            "errors": errors,
            "error_total": error_total,
            "failed_rows": failed_rows,
            "checked_rows": total - blank,
            "by_column": [
                {
                    "column": e["column"],
                    "rows": e["rows"],
                    # Rows with different values: the first one's reason is only an example.
                    "reason": f"for example {e['reason']}" if e["mixed"] else e["reason"],
                }
                for e in sorted(by_column.values(), key=lambda e: (-e["rows"], e["column"]))
            ],
            "existing_key_rows": key_only["existing"],
            "repeated_key_rows": key_only["repeated"],
            "extra_cells": (
                {
                    "rows": len(source.extra_cell_rows),
                    "examples": source.extra_cell_rows[:WARNING_EXAMPLES],
                }
                if source.extra_cell_rows
                else None
            ),
            "id_check": id_check.report() if id_check is not None else None,
        }
    return sample, total, blank, validation


def commit(
    source: ParsedSource,
    columns: list[DatasetColumn],
    mapping: dict[str, str],
    skip: set[int],
    fixes: dict[int, dict[str, Any]],
    existing_keys: set[str] | None = None,
) -> tuple[list[dict[str, Any]], int, int, list[dict[str, Any]], int]:
    """Single pass: apply mapping + skip/fixes, validate, and report the outcome.

    Returns (row_data_to_create, imported_count, skipped_count, errors, blank_skipped).
    Every source row appears in exactly one of: the created rows, the skipped
    count, the blank-line count (CSV/XLSX lines with every cell empty; tested
    before `skip`, so a retry that carries them in `skip` never counts them as
    skipped), or the errors list (never silently dropped).
    """
    columns_by_key = {col.key: col for col in columns}
    keys = _KeyCheck(key_column(columns), existing_keys or set())
    created: list[dict[str, Any]] = []
    imported = 0
    skipped = 0
    blank = 0
    errors: list[dict[str, Any]] = []

    for index, values in _iterate_rows(source):
        if is_blank_line(source, values):
            blank += 1
            continue
        if index in skip:
            skipped += 1
            continue
        data, failures = _validate_row(
            index,
            values,
            mapping,
            columns,
            columns_by_key,
            source.structured,
            fixes.get(index, {}),
            keys,
        )
        if failures:
            errors.extend(
                {"row": index, "column": key, "reason": reason} for key, reason in failures
            )
            continue
        created.append(data)
        imported += 1

    return created, imported, skipped, errors, blank
