"""Typed evidence records from result.json (contract section 2) and result.md."""
from __future__ import annotations

import hashlib
import json
import struct
import textwrap
import zlib
from pathlib import Path

import pytest

from traceflow.evidence import (
    EvidenceError,
    load_autotest_result,
    load_evidence_record,
    parse_result_md,
)


def _png(width: int, height: int) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


LEGACY_MD = textwrap.dedent(
    """\
    # PW-TEST-X-001A

    - Kind: `test`
    - Status: `passed`
    - Workflow: `.github/workflows/frontend-e2e.yml`
    - Workflow Name: Frontend E2E
    - Source: `internal/x.spec.ts::PW-TEST-X-001A: does a thing`
    - Description: Downloads 21CFR11-<docId>.pdf and checks *.png files
    and the C:\\\\Users path on a second line.
    - Run ID: `0`
    - Run URL: <unavailable>
    - Duration: `1.258s`

    ## Assertions

    - (REQ-X-001) The receipt downloads
    - (REQ-X-001, REQ-X-002) The audit entry is written
    - A check with no requirement prefix

    ## Output (last 200 lines)

    ```text
    --------------------------------- Captured Log ---------------------------------
    stderr: something went to stderr
    ```

    ## Screenshots

    **Caption:** The receipt downloads
    ![The receipt downloads](screenshots/screenshot-001.png)

    **Caption:** The audit entry is written
    ![screenshot-002.png](screenshots/screenshot-002.png)
    """
)


def _legacy_record_dir(tmp_path: Path) -> Path:
    record_dir = tmp_path / "PW-TEST-X-001A"
    (record_dir / "screenshots").mkdir(parents=True)
    (record_dir / "result.md").write_text(LEGACY_MD)
    (record_dir / "screenshots" / "screenshot-001.png").write_bytes(_png(4, 3))
    (record_dir / "screenshots" / "screenshot-002.png").write_bytes(_png(2, 5))
    (record_dir / "screenshots" / "screenshot-001.json").write_text(
        json.dumps({"caption": "The receipt downloads", "requirementId": "REQ-X-001", "checkedAt": "/documents/1"})
    )
    return record_dir


def test_result_md_maps_onto_typed_fields(tmp_path: Path) -> None:
    record_dir = _legacy_record_dir(tmp_path)
    record = load_evidence_record(str(record_dir), "PW-TEST-X-001A")

    assert record.origin == "result.md"
    assert (record.kind, record.status, record.status_label, record.passed) == ("test", "passed", "Passed", True)
    assert record.workflow == ".github/workflows/frontend-e2e.yml"
    assert record.workflow_name == "Frontend E2E"
    assert record.source == "internal/x.spec.ts::PW-TEST-X-001A: does a thing"
    # Multi-line description joined, inline HTML and literal * kept, Markdown escapes undone.
    assert record.description == (
        "Downloads 21CFR11-<docId>.pdf and checks *.png files and the C:\\Users path on a second line."
    )
    assert (record.run_id, record.run_url, record.head_sha) == (0, "", "")
    assert record.duration_s == pytest.approx(1.258)
    assert record.duration_text == "1.258 s"

    assert [a.caption for a in record.assertions] == [
        "The receipt downloads",
        "The audit entry is written",
        "A check with no requirement prefix",
    ]
    assert [a.requirement_ids for a in record.assertions] == [["REQ-X-001"], ["REQ-X-001", "REQ-X-002"], []]
    assert record.requirement_ids == ["REQ-X-001", "REQ-X-002"]
    assert [a.figure for a in record.assertions] == [0, 1, None]
    assert record.assertions[0].checked_at == "/documents/1"

    first, second = record.figures
    assert first.path == "screenshots/screenshot-001.png"
    assert first.caption == "The receipt downloads"
    assert first.requirement_ids == ["REQ-X-001"]
    assert (first.width, first.height) == (4, 3)
    assert first.sha256 == hashlib.sha256((record_dir / first.path).read_bytes()).hexdigest()
    assert first.short_sha256 == first.sha256[:12]
    assert first.size_bytes == (record_dir / first.path).stat().st_size
    # A file-name alt falls back to the preceding **Caption:** line.
    assert second.caption == "The audit entry is written"
    assert (second.width, second.height) == (2, 5)

    assert record.output.lines == ["stderr: something went to stderr"]
    assert record.output.total_lines is None
    assert record.missing_fields == []


def test_result_md_placeholders_are_empty(tmp_path: Path) -> None:
    text = textwrap.dedent(
        """\
        # PY-1

        - Kind: `test`
        - Status: `skipped`
        - Description: Skipped on purpose.

        ## Output (last 20 lines)

        ```text
        --------------------------------- Captured Out ---------------------------------
        ```

        ## Screenshots

        _No screenshots captured for this entry._
        """
    )
    record = parse_result_md(text, result_dir=str(tmp_path), evidence_id="PY-1")
    assert record.output.is_empty
    assert record.output.lines == []
    assert record.figures == []
    assert record.missing_fields == ["assertions"]
    assert record.duration_s is None
    assert record.status_label == "Skipped"


def test_result_md_heading_must_match_the_id(tmp_path: Path) -> None:
    with pytest.raises(EvidenceError, match="expected `PY-2`"):
        parse_result_md("# PY-1\n\n- Status: `passed`\n", result_dir=str(tmp_path), evidence_id="PY-2")


def _contract_json(record_dir: Path, **overrides: object) -> dict:
    image = _png(8, 6)
    (record_dir / "screenshots").mkdir(parents=True, exist_ok=True)
    (record_dir / "screenshots" / "screenshot-001.png").write_bytes(image)
    data: dict = {
        "schema": 1,
        "id": record_dir.name,
        "kind": "test",
        "status": "passed",
        "workflow_path": ".github/workflows/frontend-e2e.yml",
        "workflow_name": "Frontend E2E",
        "run_id": 123,
        "run_url": "https://github.com/o/r/actions/runs/123",
        "head_sha": "a" * 40,
        "source": "tests/integration/api/test_x.py::TestY::test_z",
        "duration_s": 0.021,
        "description": "One line",
        "description_source": "marker",
        "assertions": [
            {
                "caption": "Upload accepted",
                "requirement_ids": ["REQ-VOX-UPLOAD-051"],
                "rows": [["check", "expected", "actual"], ["status", 200, "200"]],
                "checked_at": "/upload",
                "figure": 0,
            },
            {"caption": "API only", "requirement_ids": ["REQ-VOX-UPLOAD-051"], "checked_at": None, "figure": None},
        ],
        "figures": [
            {
                "path": "screenshots/screenshot-001.png",
                "caption": "Upload accepted",
                "sha256": hashlib.sha256(image).hexdigest(),
                "width": 8,
                "height": 6,
            }
        ],
        "output": {"lines": ["line 1", "stderr: line 2"], "total_lines": 42, "truncated": True},
        "jobs": [],
    }
    data.update(overrides)
    (record_dir / "result.json").write_text(json.dumps(data))
    return data


def test_result_json_is_preferred_and_typed(tmp_path: Path) -> None:
    record_dir = tmp_path / "PW-TEST-VOX-UPLOAD-051B"
    _contract_json(record_dir)
    (record_dir / "result.md").write_text("# PW-TEST-VOX-UPLOAD-051B\n\n- Kind: `test`\n- Status: `passed`\n")

    record = load_evidence_record(str(record_dir), "PW-TEST-VOX-UPLOAD-051B")
    assert record.origin == "result.json"
    assert record.schema == 1
    assert (record.run_id, record.head_sha, record.duration_s) == (123, "a" * 40, 0.021)
    assert record.source == "tests/integration/api/test_x.py::TestY::test_z"
    assert record.description_source == "marker"
    assert record.assertions[0].rows == [["check", "expected", "actual"], ["status", "200", "200"]]
    assert record.assertions[0].figure == 0
    assert record.assertions[1].figure is None
    figure = record.figures[0]
    assert figure.requirement_ids == ["REQ-VOX-UPLOAD-051"]
    assert figure.checked_at == "/upload"
    assert figure.size_bytes == (record_dir / figure.path).stat().st_size
    assert (record.output.lines, record.output.total_lines, record.output.truncated) == (
        ["line 1", "stderr: line 2"],
        42,
        True,
    )


def test_result_json_figure_hash_must_match_the_file(tmp_path: Path) -> None:
    record_dir = tmp_path / "PY-1"
    data = _contract_json(record_dir)
    data["figures"][0]["sha256"] = "0" * 64
    (record_dir / "result.json").write_text(json.dumps(data))
    with pytest.raises(EvidenceError, match="but the file hashes to"):
        load_evidence_record(str(record_dir), "PY-1")


def test_result_json_missing_figure_file_fails(tmp_path: Path) -> None:
    record_dir = tmp_path / "PY-1"
    _contract_json(record_dir)
    (record_dir / "screenshots" / "screenshot-001.png").unlink()
    with pytest.raises(EvidenceError, match="which is not in"):
        load_evidence_record(str(record_dir), "PY-1")


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"schema": 2}, "unsupported schema 2"),
        ({"id": "PY-OTHER"}, "is for `PY-OTHER`"),
        ({"run_id": "abc"}, "run_id` must be an integer"),
        ({"duration_s": "fast"}, "duration_s` must be a number"),
        ({"assertions": [{"caption": "x", "figure": 3}]}, "is not a figure index"),
        ({"assertions": [{"caption": "x", "requirement_ids": "REQ-1"}]}, "must be a list"),
        ({"figures": [{"path": "../outside.png"}]}, "outside its record folder"),
        ({"output": {"lines": "one"}}, "must be a list"),
    ],
)
def test_result_json_contract_violations_fail(tmp_path: Path, overrides: dict, message: str) -> None:
    record_dir = tmp_path / "PY-1"
    _contract_json(record_dir, **overrides)
    with pytest.raises(EvidenceError, match=message):
        load_evidence_record(str(record_dir), "PY-1")


def test_result_json_and_md_must_agree_on_status(tmp_path: Path) -> None:
    record_dir = tmp_path / "PY-1"
    _contract_json(record_dir)
    (record_dir / "result.md").write_text("# PY-1\n\n- Kind: `test`\n- Status: `failed`\n")
    with pytest.raises(EvidenceError, match="disagrees with result.md"):
        load_evidence_record(str(record_dir), "PY-1")


def test_missing_record_files_raise(tmp_path: Path) -> None:
    (tmp_path / "PY-1").mkdir()
    with pytest.raises(FileNotFoundError, match="no result.json or result.md"):
        load_evidence_record(str(tmp_path / "PY-1"), "PY-1")


def test_load_autotest_result_keeps_legacy_parts_and_adds_the_record(tmp_path: Path) -> None:
    record_dir = _legacy_record_dir(tmp_path)
    autotest = load_autotest_result(str(record_dir), "PW-TEST-X-001A")
    assert autotest.metadata_items  # the legacy bullet list the inline renderer prints
    assert autotest.assertions_items
    assert autotest.record is not None
    assert autotest.record.figures[0].short_sha256


def test_load_autotest_result_from_json_only(tmp_path: Path) -> None:
    record_dir = tmp_path / "PY-1"
    _contract_json(record_dir, description="", assertions=[])
    autotest = load_autotest_result(str(record_dir), "PY-1")
    assert autotest.metadata_items == []
    assert autotest.missing_fields == ["description", "assertions"]
    assert autotest.record is not None and autotest.record.origin == "result.json"


def test_workflow_record_from_json_has_jobs(tmp_path: Path) -> None:
    record_dir = tmp_path / "WF-REACTJS"
    jobs = [{"name": "build", "conclusion": "success"}]
    _contract_json(record_dir, kind="workflow", figures=[], assertions=[], jobs=jobs)
    record = load_evidence_record(str(record_dir), "WF-REACTJS")
    assert record.kind == "workflow"
    assert [(job.name, job.conclusion) for job in record.jobs] == [("build", "success")]
