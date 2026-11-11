"""The report-structure model: the hierarchy layout, the document-set JSON, dates and the
CI run register (``traceflow.report_structure``)."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from traceflow.evidence import load_evidence_record
from traceflow.parser import HierarchyConfig, parse_markdown, process_directory
from traceflow.report_structure import (
    DocumentSetError,
    HierarchyError,
    area_label,
    check_commit,
    ci_run_register,
    document_set_errors,
    format_date,
    generated_date,
    parse_document_set,
    plan_hierarchy,
    short_volume_code,
    split_ius_source,
)
from traceflow.volumes import parse_manifest, plan_volumes
from tests.test_evidence_render import _write_result
from tests.test_volumes import _corpus, _manifest, _write

SHA = "ab" * 32


def _document_set(**changes: object) -> dict:
    data: dict = {
        "commit": "c4a3b477ecdba56ea6b31148c56006bad3a972c9",
        "documents": [
            {"code": "BXD-VF-019-E01", "file": "BXD-VF-019-E01.pdf", "title": "Evidence Volume E01: Ingestion",
             "tier1": ["REQ-VF-T1-001"], "pages": 9, "records": 2, "sha256": SHA},
            {"code": "BXD-VF-019-E02", "title": "Evidence Volume E02: Audit", "tier1_scope": "Audit and reporting",
             "pages": 12, "records": 4, "sha256": "cd" * 32},
        ],
    }
    data.update(changes)
    return data


# ------------------------------------------------------------------ layout


def test_plan_hierarchy_places_every_requirement_and_shell_once(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path / "corpus")
    document = process_directory(str(corpus), version="1")
    order = ["REQ-VF-T1-002", "REQ-VF-T1-001", "REQ-VF-T1-003"]
    hierarchy = plan_volumes(document, parse_manifest(_manifest())).hierarchy
    layout = plan_hierarchy(document, hierarchy, tier1_order=order)
    assert [chapter.tier1.req_id for chapter in layout.chapters] == order
    audit = layout.chapter("REQ-VF-T1-002")
    assert [section.requirement.req_id for section in audit.sections] == ["REQ-VOX-AUDITLOG-001", "REQ-VOX-21CFR11-001"]
    assert [requirement.req_id for requirement in audit.derived] == ["REQ-VOX-AUDITLOG-001", "REQ-VOX-21CFR11-001"]
    assert [test.test_id for test in audit.journey_shells] == ["TEST-VF-T1-002"]
    shells = {section.requirement.req_id: [t.test_id for t in section.shells] for section in audit.sections}
    assert shells == {
        "REQ-VOX-AUDITLOG-001": ["TEST-VOX-AUDITLOG-001"],
        "REQ-VOX-21CFR11-001": ["TEST-VOX-21CFR11-001"],
    }
    assert [t.test_id for t in layout.chapter("REQ-VF-T1-001").journey_shells] == ["TEST-VF-T1-001"]
    assert sorted(layout.shell_ids()) == sorted(t.test_id for doc in document.tests for t in doc.items)
    assert layout.areas["REQ-VOX-UPLOAD-001"] == "Families"
    assert layout.tier1_of["REQ-VOX-REPORT-001"] == ["REQ-VF-T1-003"]
    assert not layout.procedural and not layout.outside and not layout.loose_shells


def test_plan_hierarchy_keeps_procedural_and_unparented_items(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path / "corpus", extra_requirements="""
## REQ-VOX-LOOSE-001: No parent

Loose.
""", extra_tests="""
## TEST-VOX-LOOSE-001: Loose shell

**Requirement ID:** REQ-VOX-LOOSE-001

## TEST-VOX-NOREQ-001: Verifies nothing

Nothing.

## TEST-VOX-PROC-001: Procedure

**Requirement ID:** REQ-VOX-PROC-001
""")
    _write(corpus / "requirements" / "Procedural Controls.md",
           "# Procedural\n\n## REQ-VOX-PROC-001: Letter\n\nSend it.\n")
    (corpus / "config.yml").write_text(
        "name: Demo\nhierarchy:\n  top_level_prefix: REQ-VF-T1-\n  procedural_files: ['requirements/Procedural*']\n"
    )
    with pytest.raises(ValueError, match="REQ-VOX-LOOSE-001` does not trace"):
        process_directory(str(corpus), version="1")
    (corpus / "config.yml").write_text("name: Demo\n")
    document = process_directory(str(corpus), version="1")
    layout = plan_hierarchy(
        document, HierarchyConfig(top_level_prefix="REQ-VF-T1-", procedural_files=["requirements/Procedural*"])
    )
    assert [section.requirement.req_id for section in layout.procedural] == ["REQ-VOX-PROC-001"]
    assert [t.test_id for t in layout.procedural[0].shells] == ["TEST-VOX-PROC-001"]
    assert [section.requirement.req_id for section in layout.outside] == ["REQ-VOX-LOOSE-001"]
    assert [t.test_id for t in layout.outside[0].shells] == ["TEST-VOX-LOOSE-001"]
    assert [t.test_id for t in layout.loose_shells] == ["TEST-VOX-NOREQ-001"]


def test_plan_hierarchy_needs_a_tier1_prefix(tmp_path: Path) -> None:
    document = process_directory(str(_corpus(tmp_path / "corpus")), version="1")
    with pytest.raises(HierarchyError, match="needs a Tier 1 prefix"):
        plan_hierarchy(document)


def test_split_ius_source_and_area_label() -> None:
    content = parse_markdown("VoxelFlow shall ingest.\n\n**IUS Source:** BXD-VF-001 bullet 1\n\nMore.\n")
    source, rest = split_ius_source(content)
    assert source == "BXD-VF-001 bullet 1"
    assert len(rest) == 2
    assert split_ius_source(parse_markdown("Source: IUS Data Ingestion\n"))[0] == "IUS Data Ingestion"
    assert split_ius_source(parse_markdown("The source: of all things.\nSecond line.\n"))[0] == ""
    assert area_label("Audit Log Requirements") == "Audit Log"
    assert area_label("Permissions tests") == "Permissions"
    assert area_label("Requirements") == "Requirements"
    assert short_volume_code("BXD-VF-019", "BXD-VF-019-E03") == "E03"
    assert short_volume_code("BXD-VF-019", "OTHER-E03") == "OTHER-E03"


# ------------------------------------------------------------------ document set, dates, commit, CI runs


def test_document_set_parses_and_checks() -> None:
    document_set = parse_document_set(_document_set(), "set.json")
    first, second = document_set.entries
    assert (first.code, first.pages, first.records, first.tier1_ids, first.file) == (
        "BXD-VF-019-E01", 9, 2, ["REQ-VF-T1-001"], "BXD-VF-019-E01.pdf")
    assert second.tier1_scope == "Audit and reporting"
    assert parse_document_set(_document_set()["documents"]).entries[0].sha256 == SHA
    codes = ["BXD-VF-019-E01", "BXD-VF-019-E02"]
    index = {"A": {"volume": "BXD-VF-019-E01"}, "B": {"volume": "BXD-VF-019-E01"}}
    index.update({f"C{n}": {"volume": "BXD-VF-019-E02"} for n in range(4)})
    commit = "c4a3b477ecdb"
    assert document_set_errors(document_set, volume_codes=codes, index=index, commit=commit) == []
    errors = document_set_errors(document_set, volume_codes=codes + ["BXD-VF-019-E03"], index={}, commit="deadbeef0",
                                 report_code="BXD-VF-019-E01")
    assert any("no entry for volumes ['BXD-VF-019-E03']" in error for error in errors)
    assert any("says 2 records but the evidence index lists 0" in error for error in errors)
    assert any("not --commit deadbeef0" in error for error in errors)
    assert any("lists the report itself" in error for error in errors)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"documents": []}, "non-empty list"),
        ({"extra": 1}, "unknown top-level keys"),
        ({"documents": [{"code": "X", "title": "T", "pages": 1, "records": 0, "sha256": "AB" * 32}]}, "sha256"),
        ({"documents": [{"code": "X", "title": "T", "pages": 0, "records": 0, "sha256": SHA}]}, "pages must"),
        ({"documents": [{"code": "X", "title": "T", "pages": 1, "records": 0, "sha256": SHA, "colour": "red"}]},
         "unknown keys"),
        ({"documents": [{"code": "X", "title": "T", "pages": 1, "records": 0, "sha256": SHA}] * 2}, "listed twice"),
    ],
)
def test_document_set_rejects_bad_input(change: dict, message: str) -> None:
    with pytest.raises(DocumentSetError, match=message):
        parse_document_set(_document_set(**change))


def test_dates_use_english_months_and_source_date_epoch(monkeypatch: pytest.MonkeyPatch) -> None:
    moment = datetime(2026, 3, 5, tzinfo=timezone.utc)
    assert format_date(moment, "%d %B %Y") == "05 March 2026"
    assert format_date(moment, "%d %b %Y (100%%)") == "05 Mar 2026 (100%)"
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1762819200")
    assert generated_date() == "11 November 2025"
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "soon")
    with pytest.raises(ValueError, match="SOURCE_DATE_EPOCH"):
        generated_date()


def test_check_commit() -> None:
    assert check_commit(None) == "" and check_commit(" ") == ""
    assert check_commit("C4A3B477ECDB") == "c4a3b477ecdb"
    for bad in ["abc", "g" * 12, "a" * 41]:
        with pytest.raises(ValueError, match="--commit"):
            check_commit(bad)


def test_ci_run_register_lists_each_run_once(tmp_path: Path) -> None:
    results = tmp_path / "results"
    for evidence_id in ["PY-A", "PY-B", "PY-C"]:
        _write_result(results, evidence_id, status="failed" if evidence_id == "PY-C" else "passed")
    local = results / "PY-LOCAL"
    local.mkdir()
    (local / "result.md").write_text(
        (results / "PY-A" / "result.md").read_text().replace("PY-A", "PY-LOCAL")
        .replace("- Run ID: `42`\n", "").replace("- Run URL: https://github.com/example/repo/actions/runs/42\n", "")
    )
    records = [load_evidence_record(str(results / e), e) for e in ["PY-A", "PY-B", "PY-LOCAL", "PY-C"]]
    runs = ci_run_register(records)
    assert [(run.run_id, len(run.evidence_ids), run.in_ci) for run in runs] == [(42, 3, True), (None, 1, False)]
    assert runs[0].conclusion == "failed" and runs[0].failed == 1
    assert runs[0].run_url == "https://github.com/example/repo/actions/runs/42"
    assert runs[1].conclusion == "passed"
