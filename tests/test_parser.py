from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from traceflow.parser import process_directory


def _write_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalised = textwrap.dedent(content).strip()
    path.write_text(normalised + "\n")


def test_process_directory_parses_risks_and_design(tmp_path: Path) -> None:
    requirements_path = Path(tmp_path) / "requirements" / "requirements.md"
    tests_path = Path(tmp_path) / "tests" / "tests.md"
    risks_path = Path(tmp_path) / "risks" / "risk-register.md"
    design_path = Path(tmp_path) / "design" / "design-overview.md"
    docs_path = Path(tmp_path) / "docs" / "ius.md"

    _write_markdown(
        requirements_path,
        """
        # Requirements

        ## REQ-001: Login

        The system shall authenticate clinicians.
        """,
    )

    _write_markdown(
        tests_path,
        """
        # Tests

        ## TEST-001: Login succeeds

        **Requirement ID:** REQ-001
        """,
    )

    _write_markdown(
        risks_path,
        """
        # Risk Register

        Intro paragraph.

        ## RISK-001: Incorrect association

        Hazardous Situation: Images are shown for the wrong patient

        Harm: Misdiagnosis

        Cause: Race condition during message ingestion

        Severity: High

        Probability: Medium

        Controls: Requirements REQ-001 verified by TEST-001

        Residual Severity: Medium

        Residual Probability: Low
        """,
    )

    _write_markdown(
        design_path,
        """
        # Design Overview

        ## Authentication

        Refers to REQ-001.
        """,
    )

    _write_markdown(
        docs_path,
        """
        # Installation Qualification

        Ensure TEST-001 evidence is archived.
        """,
    )

    document = process_directory(str(tmp_path), version="1.0.0")

    assert len(document.risks) == 1
    risk_doc = document.risks[0]
    assert risk_doc.title == "Risk Register"
    assert len(risk_doc.items) == 1
    risk = risk_doc.items[0]
    assert risk.risk_id == "RISK-001"
    assert risk.attributes["severity"] == "High"
    assert risk.attributes["probability"] == "Medium"
    assert risk.attributes["residual_severity"] == "Medium"
    assert sorted(risk.requirement_refs) == ["REQ-001"]
    assert sorted(risk.test_refs) == ["TEST-001"]

    assert len(document.design_documents) == 1
    assert document.design_documents[0].category == "design"
    assert len(document.supplementary_documents) == 1
    assert document.supplementary_documents[0].category == "general"


def test_risk_reference_validation(tmp_path: Path) -> None:
    requirements_path = Path(tmp_path) / "requirements" / "requirements.md"
    risks_path = Path(tmp_path) / "risks" / "risk-register.md"

    _write_markdown(
        requirements_path,
        """
        # Requirements

        ## REQ-100: Example

        Some body text.
        """,
    )

    _write_markdown(
        risks_path,
        """
        # Risk Register

        ## RISK-404: Missing control

        Controls: Mitigated by REQ-999
        """,
    )

    with pytest.raises(ValueError, match="Risk `RISK-404` references requirement `REQ-999`"):
        process_directory(str(tmp_path), version="0.1.0")
