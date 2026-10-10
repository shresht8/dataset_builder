"""Parse JSON / JSONL / YAML files into records (GL-3.5-5).

Shared by the API's import and sync and the CLI's `schema infer`, so a file is
read by exactly the same rules everywhere.

- **Finding the records:** a top-level list; a Groundline export envelope
  (`{"manifest": …, "rows": [...]}`); the single key holding a list of objects;
  otherwise the caller passes `records_key`. A list whose items are all
  Groundline lines (`id`, `status`, `data`) is unwrapped to each `data`.
- **YAML:** parsed with the safe loader's composer, never constructed by
  PyYAML, so text isn't corrupted (`NO` -> False, `012` -> 10, dates -> date
  objects). Quoted and block scalars become `str`; plain scalars become
  `PlainScalar` (their raw text), typed later by the column they're mapped to;
  plain null/~/empty becomes None. Anchors/aliases and merge keys resolve,
  capped at `max_nodes` after expansion. Explicit tags and multi-document
  files are refused.
- **Problems:** a whole-file problem raises `RecordsError` (a 422). A problem
  confined to one record (a malformed JSONL line, a non-object record, a
  duplicate key) becomes a `RecordProblem` in that record's place, so the
  other records still import.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import yaml

STRUCTURED_SUFFIXES = (".json", ".jsonl", ".ndjson", ".yaml", ".yml")
DEFAULT_MAX_NODES = 1_000_000

_MERGE_TAG = "tag:yaml.org,2002:merge"
_NULL_TAG = "tag:yaml.org,2002:null"
_JSON_NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(?P<frac>\.\d+)?(?P<exp>[eE][+-]?\d+)?")
_NON_FINITE = re.compile(r"[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)")


class RecordsError(ValueError):
    """The file as a whole can't be read (a 422)."""

    def __init__(self, message: str, candidates: list[str] | None = None) -> None:
        super().__init__(message)
        # Set when the caller must choose a records_key: the keys holding lists.
        self.candidates = candidates


class PlainScalar(str):
    """The raw text of a plain (unquoted) YAML scalar, not yet typed."""


class _KeyedDict(dict):
    """A mapping that had repeated keys; the later value won, but it's an error."""

    duplicates: list[str]


@dataclass
class RecordProblem:
    """A record that can't be imported; reported as a row error."""

    reason: str


@dataclass
class ParsedRecords:
    records: list[Any]  # dict per record, or RecordProblem
    records_key: str | None = None
    ignored_keys: list[str] = field(default_factory=list)


def decode_text(content: bytes, *, csv: bool = False) -> str:
    """UTF-8, with or without a BOM; anything else is refused, not mangled."""
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        hint = ' — save it as "CSV UTF-8"' if csv else ""
        raise RecordsError(f"file is not UTF-8{hint}") from None


def to_json_value(value: Any) -> Any:
    """Type a parsed value the way JSON would, for a `json` column.

    Plain YAML scalars: `true`/`false` -> bool, JSON-syntax numbers -> numbers,
    anything else stays text (so `yes`, `NO`, `012` and dates stay strings).
    Raises ValueError for `.inf`/`.nan`, which JSON can't represent.
    """
    if isinstance(value, PlainScalar):
        text = str(value)
        if text in ("true", "false"):
            return text == "true"
        match = _JSON_NUMBER.fullmatch(text)
        if match:
            return float(text) if match["frac"] or match["exp"] else int(text)
        if _NON_FINITE.fullmatch(text):
            raise ValueError(f"{text} is not representable in JSON")
        return text
    if isinstance(value, dict):
        return {key: to_json_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_json_value(item) for item in value]
    if isinstance(value, str):
        return str(value)
    return value


def parse_records(
    filename: str,
    content: bytes,
    records_key: str | None = None,
    max_nodes: int = DEFAULT_MAX_NODES,
) -> ParsedRecords:
    """Parse a .json / .jsonl / .ndjson / .yaml / .yml file into records."""
    lower = filename.lower()
    text = decode_text(content)
    if lower.endswith((".jsonl", ".ndjson")):
        return _finish(_jsonl_records(text), None, [])
    if lower.endswith(".json"):
        doc = _json_document(text)
    elif lower.endswith((".yaml", ".yml")):
        doc = _yaml_document(text, max_nodes)
    else:
        raise RecordsError(f"unsupported file type: {filename!r}")
    records, used_key, ignored = _find_records(doc, records_key)
    return _finish(records, used_key, ignored)


# --- JSON ---------------------------------------------------------------


def _pairs(pairs: list[tuple[str, Any]]) -> dict:
    result: dict = {}
    duplicates = []
    for key, value in pairs:
        if key in result:
            duplicates.append(key)
        result[key] = value
    if duplicates:
        keyed = _KeyedDict(result)
        keyed.duplicates = duplicates
        return keyed
    return result


def _refuse_constant(name: str) -> Any:
    raise ValueError(f"{name} is not valid JSON")


def _json_loads(text: str) -> Any:
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=_refuse_constant)


def _json_document(text: str) -> Any:
    if not text.strip():
        return None
    try:
        return _json_loads(text)
    except json.JSONDecodeError as exc:
        if exc.msg == "Extra data":
            raise RecordsError(
                "this looks like JSON Lines (one object per line) — save it as .jsonl"
            ) from None
        raise RecordsError(f"invalid JSON: {exc}") from None
    except ValueError as exc:
        raise RecordsError(str(exc)) from None


def _jsonl_records(text: str) -> list[Any]:
    records: list[Any] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(_json_loads(line))
        except ValueError as exc:  # JSONDecodeError or a NaN/Infinity constant
            records.append(RecordProblem(f"line {line_number}: invalid JSON ({exc})"))
    return records


# --- YAML ---------------------------------------------------------------


def _yaml_document(text: str, max_nodes: int) -> Any:
    try:
        documents = 0
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.DocumentStartEvent):
                documents += 1
                if documents > 1:
                    raise RecordsError("multi-document YAML is not supported")
            elif isinstance(
                event, (yaml.ScalarEvent, yaml.SequenceStartEvent, yaml.MappingStartEvent)
            ) and event.tag not in (None, "!"):
                raise RecordsError(f"YAML tags are not allowed (found {event.tag})")
        if documents == 0:
            return None
        node = yaml.compose(text, Loader=yaml.SafeLoader)
    except yaml.YAMLError as exc:
        raise RecordsError(f"invalid YAML: {exc}") from None
    except RecursionError:
        raise RecordsError("YAML document is nested too deeply") from None
    try:
        return _YamlConverter(max_nodes).convert(node)
    except RecursionError:
        raise RecordsError("YAML document is nested too deeply") from None


class _YamlConverter:
    """Turns composed YAML nodes into values, expanding aliases with a budget."""

    def __init__(self, max_nodes: int) -> None:
        self.max_nodes = max_nodes
        self.count = 0
        self.open: set[int] = set()  # nodes being converted: an alias to one is a cycle

    def convert(self, node: yaml.Node) -> Any:
        self.count += 1
        if self.count > self.max_nodes:
            raise RecordsError(
                f"YAML expands to more than {self.max_nodes} values (check anchors/aliases)"
            )
        if isinstance(node, yaml.ScalarNode):
            if node.style is None:
                return None if node.tag == _NULL_TAG else PlainScalar(node.value)
            return node.value
        if id(node) in self.open:
            raise RecordsError("YAML alias refers to itself")
        self.open.add(id(node))
        try:
            if isinstance(node, yaml.SequenceNode):
                return [self.convert(item) for item in node.value]
            return self._mapping(node)
        finally:
            self.open.discard(id(node))

    def _mapping(self, node: yaml.MappingNode) -> dict:
        merged: dict = {}
        explicit: dict = {}
        duplicates: list[str] = []
        for key_node, value_node in node.value:
            if key_node.tag == _MERGE_TAG:
                sources = (
                    value_node.value if isinstance(value_node, yaml.SequenceNode) else [value_node]
                )
                for source in sources:
                    values = self.convert(source)
                    if not isinstance(values, dict):
                        raise RecordsError("a YAML merge key (<<) must point to a mapping")
                    for key, value in values.items():
                        merged.setdefault(key, value)  # earlier sources win
                continue
            if not isinstance(key_node, yaml.ScalarNode):
                raise RecordsError("YAML mapping keys must be plain values")
            key = key_node.value
            if key in explicit:
                duplicates.append(key)
            explicit[key] = self.convert(value_node)
        result = {**merged, **explicit}
        if duplicates:
            keyed = _KeyedDict(result)
            keyed.duplicates = duplicates
            return keyed
        return result


# --- Records ------------------------------------------------------------


def _is_groundline_line(record: Any) -> bool:
    return (
        isinstance(record, dict)
        and set(record) == {"id", "status", "data"}
        and isinstance(record["data"], dict)
    )


def _find_records(doc: Any, records_key: str | None) -> tuple[list, str | None, list[str]]:
    if doc is None:
        return [], None, []
    if isinstance(doc, list):
        return doc, None, []
    if not isinstance(doc, dict):
        raise RecordsError("expected a list of records, or an object containing one")
    if getattr(doc, "duplicates", None):
        raise RecordsError(f"duplicate top-level key '{doc.duplicates[0]}'")

    list_keys = [key for key, value in doc.items() if isinstance(value, list)]
    if records_key is not None:
        if not isinstance(doc.get(records_key), list):
            raise RecordsError(
                f"records_key '{records_key}' is not a list in this file", candidates=list_keys
            )
        chosen = records_key
    elif isinstance(doc.get("manifest"), dict) and isinstance(doc.get("rows"), list):
        chosen = "rows"  # a Groundline json/yaml export
    else:
        object_lists = [
            key for key in list_keys if all(isinstance(item, dict) for item in doc[key])
        ]
        if len(object_lists) != 1:
            raise RecordsError(
                "can't tell which key holds the records — choose a records_key"
                + (f" (lists found: {', '.join(list_keys)})" if list_keys else ""),
                candidates=list_keys,
            )
        chosen = object_lists[0]
    return doc[chosen], chosen, [key for key in doc if key != chosen]


def _duplicates(value: Any) -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        found.extend(getattr(value, "duplicates", []))
        for item in value.values():
            found.extend(_duplicates(item))
    elif isinstance(value, list):
        for item in value:
            found.extend(_duplicates(item))
    return found


def _finish(records: list, records_key: str | None, ignored: list[str]) -> ParsedRecords:
    objects = [r for r in records if not isinstance(r, RecordProblem)]
    if objects and all(_is_groundline_line(r) for r in objects):
        records = [r if isinstance(r, RecordProblem) else r["data"] for r in records]

    checked: list[Any] = []
    for record in records:
        if isinstance(record, RecordProblem):
            checked.append(record)
        elif not isinstance(record, dict):
            checked.append(RecordProblem("record is not an object"))
        elif duplicates := _duplicates(record):
            checked.append(RecordProblem(f"duplicate key '{duplicates[0]}'"))
        else:
            checked.append(record)
    return ParsedRecords(records=checked, records_key=records_key, ignored_keys=ignored)
