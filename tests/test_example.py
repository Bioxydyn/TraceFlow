"""The example in example/ still builds: example/build.sh runs, pdflatex and mermaid stubbed."""
from __future__ import annotations

import os
import shutil
import subprocess  # noqa: S404
import sys
from pathlib import Path

import pytest

from traceflow.evidence import load_evidence_record
from traceflow.parser import process_directory

ROOT = Path(__file__).resolve().parents[1]
EXAMPLE = ROOT / "example"


def test_the_example_corpus_parses_with_its_hierarchy() -> None:
    document = process_directory(str(EXAMPLE / "source"), version="1.0.0")
    requirements = [item.req_id for section in document.requirements for item in section.items]
    tests = [item.test_id for section in document.tests for item in section.items]
    risks = [item for section in document.risks for item in section.items]
    assert requirements == ["REQ-T1-001", "REQ-T1-002", "REQ-001", "REQ-002", "REQ-003", "REQ-004", "REQ-005"]
    assert tests == [f"TEST-00{n}" for n in range(1, 7)]
    # Every risk names the requirement that controls it and the tests that show the control works.
    assert risks and all(risk.requirement_refs and risk.test_refs for risk in risks)


def test_the_example_evidence_records_load() -> None:
    for folder in sorted((EXAMPLE / "test-results").iterdir()):
        record = load_evidence_record(str(folder), folder.name)
        assert record.status == "passed" and record.assertions, folder.name


@pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")
def test_build_script_renders_the_three_documents(tmp_path: Path) -> None:
    env = {
        **os.environ,
        "TRACEFLOW": f"{sys.executable} -m tests.traceflow_stubbed",
        "PYTHONPATH": str(ROOT),
        "OUT": str(tmp_path),
    }
    command = ["bash", str(EXAMPLE / "build.sh")]
    result = subprocess.run(command, env=env, capture_output=True, text=True, check=False)  # noqa: S603, S607
    assert result.returncode == 0, result.stdout + result.stderr
    for name in ["DemoScan-Validation-Pack.pdf", "DemoScan-Traceability-Report.pdf", "DemoScan-Risk-Register.pdf"]:
        assert (tmp_path / name).is_file(), name

    # The validation pack prints every evidence record and keeps its cover sign-off.
    pack = (tmp_path / "build" / "validation-pack" / "traceflow.tex").read_text(encoding="utf-8")
    assert "Validation pack result approved by" in pack
    for record in ["E2E-LOGIN-001", "UNIT-RECONCILE-001", "UNIT-IMPORT-001", "CI-PIPELINE-CHECKS"]:
        assert f"\\label{{ev:{record}}}" in pack, record
    # The traceability report has a chapter per Tier 1 requirement, a risk trace and no sign-off.
    report = (tmp_path / "build" / "traceability-report" / "traceflow.tex").read_text(encoding="utf-8")
    for tier1 in ["REQ-T1-001", "REQ-T1-002"]:
        assert f"\\section{{\\texorpdfstring{{\\mbox{{\\hyperref[{tier1}]" in report, tier1
    assert "\\section{Release results}" in report and "\\section{Risk trace}" in report
    assert "Validation pack result approved by" not in report and "\\rule{6cm}" not in report
    register = (tmp_path / "build" / "risk-register" / "traceflow.tex").read_text(encoding="utf-8")
    assert all(f"RISK-00{n}" in register for n in range(1, 7))
