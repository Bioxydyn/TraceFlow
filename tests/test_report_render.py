"""Rendering fixes in every mode: the Risk Register heading inside its A3 block (TF-11) and
the manual-test form kept on one page with stable field names (TF-12).

pdflatex is replaced by a stub (``test_evidence_render._FakeLatex``).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

import traceflow.pdf_generator as pdf_generator
from traceflow.parser import process_directory
from traceflow.pdf_generator import PdfReport
from tests.test_evidence_render import _FakeLatex, _corpus_with_results
from tests.test_volumes import _write

RISKS = """
# Risk Register

The register.

## RISK-VOX-001: Lost upload

Controls: Uploads are checked (REQ-VOX-UPLOAD-001).

Test evidence: TEST-VOX-UPLOAD-001, TEST-VOX-AUDITLOG-001.

## RISK-VOX-002: Unchecked thing

Controls: A procedure.

Test evidence: CI workflows block disallowed licences.
"""


@pytest.fixture
def fake_latex(monkeypatch: pytest.MonkeyPatch) -> _FakeLatex:
    fake = _FakeLatex()
    monkeypatch.setattr(pdf_generator.subprocess, "check_output", fake)
    return fake


def _add_risks(corpus: Path) -> None:
    _write(corpus / "risks" / "risk-register.md", RISKS)


def _hierarchy_corpus(tmp_path: Path) -> tuple[Path, Path]:
    corpus, results = _corpus_with_results(tmp_path)
    _add_risks(corpus)
    return corpus, results


def test_risk_register_heading_sits_in_its_a3_block(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    corpus, results = _hierarchy_corpus(tmp_path)
    report = PdfReport(process_directory(str(corpus), version="2.0"), test_results_dir=str(results))
    report.render(include_design=False, include_supplementary=False, include_requirements=False,
                  include_tests=False, work_dir=str(tmp_path / "work"))
    tex = fake_latex.tex
    a3 = tex.split("\\pdfpagewidth=420mm")[1]
    assert tex.count("\\subsection{Risk Register}") == 1 and "\\subsection{Risk Register}" in a3
    assert "\\subsection{Risk Register}\n\n\\small" in a3
    # One ID per line in the Test Evidence cell, breakable; prose stays prose.
    assert "{\\raggedright TEST-\\allowbreak{}VOX-\\allowbreak{}UPLOAD-\\allowbreak{}001 \\newline TEST-" in a3
    assert "{\\raggedright CI workflows block disallowed licences.}" in a3


def test_manual_test_form_stays_together_with_stable_field_names(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    corpus = tmp_path / "uat"
    _write(corpus / "tests" / "UAT Tests.md", """
    # UAT

    ## TEST-UAT-001: One

    Try it.

    ```manualtest
    ```

    ## TEST-UAT-002: Two

    ```manualtest
    ```
    """)
    report = PdfReport(process_directory(str(corpus), version="1.0"))
    report.render(work_dir=str(tmp_path / "w1"))
    first = fake_latex.tex
    report.render(work_dir=str(tmp_path / "w2"))
    assert first == fake_latex.tex
    assert first.count("\\begin{minipage}{\\linewidth}\n\\begin{Form}") == 2
    assert first.count("\\end{Form}\n\\end{minipage}") == 2
    names = re.findall(r"name=([A-Z0-9]{10})", first)
    assert len(names) == 8 and len(set(names)) == 8
