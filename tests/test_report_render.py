"""Rendering fixes in every mode: the Risk Register heading inside its A3 block (TF-11) and
the manual-test form kept on one page with stable field names (TF-12); the cover rows, footer
and date (TF-07); reproducible builds; the Verified by line (TF-06) and list matrices (TF-04).

pdflatex is replaced by a stub (``test_evidence_render._FakeLatex``).
"""
from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any, Optional

import pytest

import traceflow.__main__ as traceflow_main
import traceflow.pdf_generator as pdf_generator
from traceflow.parser import process_directory
from traceflow.pdf_generator import PdfReport
from tests.test_evidence_render import _FakeLatex, _corpus_with_results
from traceflow.volumes import parse_manifest, plan_volumes
from tests.test_volumes import _corpus, _manifest, _write

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


def _render_files(tmp_path: Path, **options: Any) -> None:  # noqa: ANN401
    corpus, results = _hierarchy_corpus(tmp_path)
    report = PdfReport(process_directory(str(corpus), version="2.0"), test_results_dir=str(results))
    report.render(include_design=False, include_supplementary=False, work_dir=str(tmp_path / "work"), **options)


def test_cover_rows_footer_and_date_defaults_are_unchanged(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    _render_files(tmp_path)
    tex = fake_latex.tex
    assert "\\textbf{Version under test} &  \\\\" in tex and "\\textbf{Engineer} &  \\\\" in tex
    assert "\\fancyfoot[L]{\\footnotesize \\traceflowdocumentcode{} | Report produced using TraceFlow" in tex
    assert "\\textbf{Generated:} \\today" in tex
    assert "\\pdftrailerid" not in tex


def test_cover_rows_fill_or_drop_and_the_release_footer(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    _render_files(tmp_path, cover_version="Demo 2.0 (commit c4a3b477ecdb)", cover_engineer="",
                  commit="C4A3B477ECDBA56EA6B31148", date_format="%d %B %Y")
    tex = fake_latex.tex
    assert "\\textbf{Version under test} & Demo 2.0 (commit c4a3b477ecdb) \\\\" in tex
    assert "\\textbf{Engineer}" not in tex
    footer = re.search(r"\\fancyfoot\[L\]\{\\footnotesize (.*)\}", tex)
    assert footer is not None
    assert re.fullmatch(r"\\traceflowdocumentcode\{\} \| .* 2\.0 \(c4a3b477ecdb\) \| TraceFlow \S+", footer.group(1))
    assert re.search(r"\\textbf\{Generated:\} \d\d [A-Z][a-z]+ \d{4}", tex)
    assert re.search(r"Initial Report Generation Date\} & \d\d [A-Z][a-z]+ \d{4}", tex)


class _EnvLatex(_FakeLatex):
    def __init__(self) -> None:
        super().__init__()
        self.env: Optional[dict] = None

    def __call__(self, command: str, **kwargs: object) -> str:
        self.env = kwargs.get("env")  # type: ignore[assignment]
        return super().__call__(command, **kwargs)


def test_source_date_epoch_fixes_the_trailer_id_and_dates(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _EnvLatex()
    monkeypatch.setattr(pdf_generator.subprocess, "check_output", fake)
    corpus, results = _hierarchy_corpus(tmp_path)
    report = PdfReport(process_directory(str(corpus), version="2.0"), test_results_dir=str(results))
    report.render(include_design=False, include_supplementary=False, work_dir=str(tmp_path / "a"))
    assert fake.env is None and "\\pdftrailerid" not in fake.tex
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1762819200")
    report.render(include_design=False, include_supplementary=False, work_dir=str(tmp_path / "b"),
                  date_format="%d %B %Y")
    first = fake.tex
    assert fake.env is not None and fake.env["FORCE_SOURCE_DATE"] == "1"
    assert first.startswith("\\pdftrailerid{") and "\\pdfsuppressptexinfo=-1" in first.splitlines()[0]
    assert "\\textbf{Generated:} 11 November 2025" in first
    report.render(include_design=False, include_supplementary=False, work_dir=str(tmp_path / "c"),
                  date_format="%d %B %Y")
    assert fake.tex == first


@pytest.mark.skipif(shutil.which("pdflatex") is None, reason="needs pdflatex")
def test_two_builds_of_the_same_input_are_byte_identical(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    corpus, results = _corpus_with_results(tmp_path, figure=True)
    _add_risks(corpus)
    _write(corpus / "tests" / "UAT Tests.md", "# UAT\n\n## TEST-UAT-001: One\n\n```manualtest\n```\n")
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1762819200")
    outputs = []
    for name in ["first", "second"]:
        report = PdfReport(process_directory(str(corpus), version="2.0"), test_results_dir=str(results))
        outputs.append(
            report.render(include_design=False, include_supplementary=False, work_dir=str(tmp_path / name),
                          document_code="BXD-X", commit="c4a3b477ecdb", date_format="%d %B %Y")
        )
    assert outputs[0] == outputs[1]
    assert b"/CreationDate (D:20251111000000Z)" in outputs[0]


def test_evidence_volume_uses_the_release_footer_and_date(
    tmp_path: Path, fake_latex: _FakeLatex, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SOURCE_DATE_EPOCH", "1762819200")
    corpus, results = _corpus_with_results(tmp_path)
    document = process_directory(str(corpus), version="2.0")
    plan = plan_volumes(document, parse_manifest(_manifest()))
    PdfReport(document, test_results_dir=str(results)).render_evidence_volume(
        plan, "BXD-VF-019-E01", work_dir=str(tmp_path / "w"), commit="c4a3b477ecdb"
    )
    tex = fake_latex.tex
    assert "(c4a3b477ecdb) | TraceFlow" in tex
    assert "Initial Report Generation Date} & 11 November 2025" in tex
    assert "\\today" not in tex


def test_cli_rejects_a_bad_commit(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    rc = traceflow_main._run(str(tmp_path), "1.0", str(tmp_path / "out.pdf"), commit="xyz")
    assert rc == 2
    assert "--commit must be a git commit SHA" in capsys.readouterr().err


def test_cli_passes_cover_and_footer_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class _Stub:
        def __init__(self, document: object, **_: object) -> None:
            pass

        def render(self, **kwargs: object) -> bytes:
            captured.update(kwargs)
            return b"%PDF-FAKE"

    monkeypatch.setattr(traceflow_main, "process_directory", lambda directory, version: object())
    monkeypatch.setattr(traceflow_main, "PdfReport", _Stub)
    rc = traceflow_main._run(
        str(tmp_path), "1.0", str(tmp_path / "out.pdf"), cover_version="Demo 1.0 (commit abc1234)",
        cover_engineer="", commit="abc1234", date_format="%d %B %Y",
    )
    assert rc == 0
    assert (captured["cover_version"], captured["cover_engineer"]) == ("Demo 1.0 (commit abc1234)", "")
    assert (captured["commit"], captured["date_format"]) == ("abc1234", "%d %B %Y")


def test_verified_by_replaces_the_run_on_test_id_lines(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    _render_files(tmp_path)
    tex = fake_latex.tex
    assert "Test ID:" not in tex
    upload = tex.split("\\label{REQ-VOX-UPLOAD-001}")[1].split("\\label{REQ-VOX-AUDITLOG-001}")[0]
    assert (
        "{\\raggedright\\noindent\\textbf{Verified by:} \\hyperref[TEST-VOX-UPLOAD-001]{\\textbf{TEST-\\allowbreak{}"
        in upload
    )
    assert "(2 evidence records)\\par}" in upload
    # The default matrix is still the tick matrix.
    assert "\\diagbox" in tex


def test_list_matrix_is_a_portrait_longtable(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    corpus, results = _hierarchy_corpus(tmp_path)
    _write(corpus / "requirements" / "Untested Requirements.md",
           "# Untested Requirements\n\n## REQ-VOX-UNTESTED-001: Nobody checks\n\nParent: REQ-VF-T1-003\n")
    report = PdfReport(process_directory(str(corpus), version="2.0"), test_results_dir=str(results))
    report.render(include_design=False, include_supplementary=False, matrix_style="list", traceability_a3=True,
                  work_dir=str(tmp_path / "work"))
    tex = fake_latex.tex
    assert "\\diagbox" not in tex and "\\pdfpagewidth=420mm" in tex  # only the risk register stays A3
    matrix = tex.split("\\subsection{Traceability Matrix}")[1].split("\\end{longtable}")[0]
    assert "\\textbf{Requirement} & \\textbf{Title} & \\textbf{Parent} & \\textbf{Verified by}" in matrix
    assert "\\clearpage" not in matrix and "\\endhead" in matrix
    untested = tex.split("\\section{Untested Requirements}")[1].split("\\end{longtable}")[0]
    assert "\\rowcolor{red!18}\\hyperref[REQ-VOX-UNTESTED-001]" in untested
    assert "& -- & -- \\\\" in untested


def test_list_matrix_marks_shells_without_evidence(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    corpus = _corpus(tmp_path / "corpus", extra_tests="""
## TEST-VOX-REPORT-002: Manual check

**Requirement ID:** REQ-VOX-REPORT-001

```manualtest
```
""")
    report = PdfReport(process_directory(str(corpus), version="2.0"))
    report.render(include_design=False, include_supplementary=False, include_tests=False, matrix_style="list",
                  work_dir=str(tmp_path / "work"))
    matrix = fake_latex.tex.split("REQ-VOX-REPORT-001]")[1]
    # Without test results the records are only counted.
    assert "1 bound" in matrix.split("\\\\")[0]
