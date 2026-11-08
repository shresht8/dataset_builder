"""GL-117: POST /v1/schema/infer (shared inference, same result as the CLI)."""

from __future__ import annotations

import uuid
from pathlib import Path

from groundline_api.config import settings
from groundline_schema.infer import infer_columns
from groundline_schema.records import RecordProblem, parse_records

FIXTURE = Path(__file__).parent / "fixtures" / "eval_suite.yaml"


def _infer(client, name: str, content: bytes, **form):
    return client.post("/v1/schema/infer", files={"file": (name, content)}, data=form)


def test_eval_suite_matches_the_cli_function(client, as_role):
    as_role("editor")
    content = FIXTURE.read_bytes()
    resp = _infer(client, "eval_suite.yaml", content)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    records = [r for r in parse_records("eval_suite.yaml", content).records if not isinstance(r, RecordProblem)]
    expected, _ = infer_columns(records)
    assert body["columns"] == [{k: v for k, v in c.items() if k != "_comment"} for c in expected]
    assert body["record_count"] == 4

    types = {c["key"]: c["type"] for c in body["columns"]}
    assert types["id"] == "text" and next(c for c in body["columns"] if c["key"] == "id")["is_key"]
    assert "json" in types.values() and "multi_select" in types.values()
    assert "long_text" in types.values() and "number" in types.values()
    assert any("thread_id" in w and "only nulls" in w for w in body["warnings"])


def test_infer_csv_key_size_and_rbac(client, as_role, monkeypatch):
    as_role("editor")
    csv = b"id,question,asked_on\nq1,What?,2099-01-01\nq2,\"Line\nbreak\",2099-01-02\n"
    resp = _infer(client, "q.csv", csv)
    assert resp.status_code == 200, resp.text
    types = {c["key"]: c["type"] for c in resp.json()["columns"]}
    assert types == {"id": "text", "question": "long_text", "asked_on": "date"}

    resp = _infer(client, "q.csv", csv, key="missing")
    assert resp.status_code == 422 and "'missing'" in resp.json()["detail"]

    monkeypatch.setattr(settings, "import_max_bytes", 10)
    assert _infer(client, "q.csv", csv).status_code == 413
    monkeypatch.undo()

    as_role("annotator")
    assert _infer(client, "q.csv", csv).status_code == 403


def test_inferred_columns_round_trip_through_put(client, as_role, cleanup):
    as_role("editor")
    columns = _infer(client, "eval_suite.yaml", FIXTURE.read_bytes()).json()["columns"]
    ds = client.post("/v1/datasets", json={"name": f"inf-{uuid.uuid4().hex[:8]}"}).json()
    cleanup["datasets"].append(ds["id"])
    resp = client.put(f"/v1/datasets/{ds['id']}/schema", json={"columns": columns})
    assert resp.status_code == 200, resp.text
    assert [c["key"] for c in resp.json()["columns"]] == [c["key"] for c in columns]
