"""Rendering the signed report's structure: the hierarchy chapters, list matrices, the
document-set table, the CI run register, the risk trace table and the appendices; the TF-06,
TF-07, TF-11 and TF-12 fixes; reproducible builds; and the CLI flags.

pdflatex is replaced by a stub (``test_evidence_render._FakeLatex``) unless a test says otherwise.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any, Optional

import pytest

import traceflow.__main__ as traceflow_main
import traceflow.pdf_generator as pdf_generator
from traceflow.evidence_render import load_evidence_index, write_evidence_index
from traceflow.parser import process_directory
from traceflow.pdf_generator import PdfReport
from traceflow.report_structure import DocumentSetError, HierarchyError, parse_document_set
from traceflow.volumes import parse_manifest, plan_volumes
from tests.test_evidence_render import _FakeLatex, _corpus_with_results
from tests.test_report_structure import _document_set
from tests.test_volumes import _corpus, _manifest, _write

ALL_IDS = ["PY-UPLOAD", "PW-SHARED-X", "PY-AUDIT-CLAUSE", "PY-CLAUSE-ONLY", "PY-REPORT", "PW-JOURNEY"]

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


def _index(tmp_path: Path) -> dict[str, dict]:
    volumes = {"PY-UPLOAD": "BXD-VF-019-E01", "PW-JOURNEY": "BXD-VF-019-E01"}
    entries = {
        evidence_id: {"volume": volumes.get(evidence_id, "BXD-VF-019-E02"), "page": n, "status": "passed"}
        for n, evidence_id in enumerate(ALL_IDS, start=3)
    }
    path = tmp_path / "index.json"
    write_evidence_index(str(path), entries)
    return load_evidence_index([str(path)])


def _render_hierarchy(tmp_path: Path, **options: Any) -> tuple[PdfReport, dict]:  # noqa: ANN401
    corpus, results = _hierarchy_corpus(tmp_path)
    document = process_directory(str(corpus), version="2.0")
    plan = plan_volumes(document, parse_manifest(_manifest()))
    report = PdfReport(document, test_results_dir=str(results))
    kwargs: dict = {
        "include_design": False, "include_supplementary": False, "structure": "hierarchy",
        "volume_plan": plan, "evidence_mode": "reference", "evidence_index": _index(tmp_path),
        "document_code": "BXD-VF-019", "work_dir": str(tmp_path / "work"),
    }
    kwargs.update(options)
    report.render(**kwargs)
    return report, kwargs


# ------------------------------------------------------------------ the hierarchy report


def test_hierarchy_report_has_tier1_chapters_and_every_item_once(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    _render_hierarchy(tmp_path)
    tex = fake_latex.tex
    # Chapters in manifest order, each a section, with labels for every requirement and shell once.
    chapters = re.findall(r"\\section\{[^\n]*?(REQ-VF-T1-00\d)\]", tex)
    assert chapters == ["REQ-VF-T1-001", "REQ-VF-T1-002", "REQ-VF-T1-003"]
    for item_id in ["REQ-VF-T1-001", "REQ-VOX-UPLOAD-001", "REQ-VOX-AUDITLOG-001", "REQ-VOX-21CFR11-001",
                    "TEST-VOX-UPLOAD-001", "TEST-VF-T1-001", "TEST-VOX-REPORT-001", "RISK-VOX-001"]:
        assert tex.count(f"\\label{{{item_id}}}") == 1, item_id
    # The derived-requirements table, journey shells, Tier 2 lines.
    assert "\\subsection*{Derived requirements}" in tex
    assert "\\subsection*{Tier 1 journey}" in tex
    audit = tex.split("\\label{REQ-VOX-AUDITLOG-001}")[1].split("\\label{TEST-VOX-AUDITLOG-001}")[0]
    assert "\\textbf{Parent:} \\hyperref[REQ-VF-T1-002]" in audit
    assert "\\textbf{Verified by:} \\hyperref[TEST-VOX-AUDITLOG-001]" in audit
    assert "\\textbf{Risks:} \\hyperref[RISK-VOX-001]" in audit
    assert "Area: Families" in audit
    # Shells under their requirement, a level lower, with reference rows.
    assert "\\subsubsection{\\mbox{\\hyperref[TEST-VOX-AUDITLOG-001]" in tex
    assert "\\mbox{BXD-VF-019-E02} \\mbox{p.~" in tex
    # Order: journey shells come after the derived table and before the Tier 2 statements.
    t1 = tex.split("\\label{REQ-VF-T1-001}")[1].split("\\label{REQ-VF-T1-002}")[0]
    positions = [t1.index(marker) for marker in
                 ["Derived requirements", "\\label{TEST-VF-T1-001}", "\\label{REQ-VOX-UPLOAD-001}"]]
    assert positions == sorted(positions)
    # No per-file sections, no A3 tick matrix, no copy of the risk register.
    assert "\\diagbox" not in tex and "Risk Scoring Method" not in tex and "\\subsection{Risk Register}" not in tex
    assert "\\section{Families}" not in tex
    # Contents at depth 1 by default; generated date in "%d %B %Y"; release footer.
    assert "\\setcounter{tocdepth}{1}" in tex
    assert "\\textbf{Generated:} \\today" not in tex
    # The release footer, with no commit given: "<code> | <name> <version> | TraceFlow <version>".
    assert re.search(r"\\fancyfoot\[L\]\{\\footnotesize \\traceflowdocumentcode\{\} \| \S+ 2\.0 \| TraceFlow ", tex)


def test_hierarchy_rollup_table_counts_evidence_and_volumes(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    _render_hierarchy(tmp_path)
    tex = fake_latex.tex
    t1 = tex.split("\\label{REQ-VF-T1-002}")[1].split("\\label{REQ-VF-T1-003}")[0]
    rollup = t1.split("Derived requirements")[2].split("\\end{longtable}")[0]
    assert "\\textbf{Requirement} & \\textbf{Title} & \\textbf{Area} & \\textbf{Shells}" in rollup
    assert "\\endhead" in rollup
    # REQ-VOX-AUDITLOG-001 binds PY-AUDIT-CLAUSE and PW-SHARED-X, both passed, in volume E02.
    row = next(line for line in rollup.splitlines() if line.startswith("\\hyperref[REQ-VOX-AUDITLOG-001]"))
    assert "{\\textbf{2/2}}" in row and "\\mbox{E02}" in row and "\\hyperref[RISK-VOX-001]" in row
    assert "2 derived requirements, 3 test shells and 3 evidence records, of which 3 passed." in t1


def test_hierarchy_front_matter_register_and_appendices(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    _render_hierarchy(tmp_path, cover_result="Passed", commit="c4a3b477ecdb", risk_register_ref="BXD-VF-020")
    tex = fake_latex.tex
    front = tex.split("\\section{How to read this report}")[1].split("\\label{REQ-VF-T1-001}")[0]
    assert "\\textbf{Result:} Passed" in front
    assert "Distinct evidence records & 6" in front and "Evidence bindings & 10" in front
    assert "\\subsection{Evidence records per volume}" in front
    # The CI run register lists run 42 once, with its link, for all six records.
    register = front.split("\\subsection{CI run register}")[1]
    assert register.count("actions/runs/42") == 1
    assert "Python package & \\href{https://github.com/example/repo/actions/runs/42}{42} & -- & 6 &" in register
    assert "A head commit other than c4a3b477ecdb is shown in red." in register
    # Risk trace table instead of the register; red row for the risk with no shell.
    trace = tex.split("\\section{Risk trace}")[1].split("\\section{Appendix A")[0]
    assert "is BXD-VF-020." in trace
    assert "\\leavevmode\\phantomsection\\label{RISK-VOX-001}" in trace
    assert "\\rowcolor{red!18}\\leavevmode\\phantomsection\\label{RISK-VOX-002}" in trace
    # Appendix A: areas with pages; Appendix B: every record with its volume and page.
    areas = tex.split("\\section{Appendix A: Requirements by area}")[1].split("\\section{Appendix B")[0]
    assert "\\pageref{REQ-VOX-UPLOAD-001}" in areas and "Source file: \\texttt{requirements/" in areas
    index = tex.split("\\section{Appendix B: Evidence index}")[1]
    assert index.count("\\\\") >= 6
    # PY-UPLOAD links to the first shell printed that binds it: the Tier 1 journey shell.
    assert "\\hyperref[TEST-VF-T1-001]{PY-\\allowbreak{}UPLOAD} &" in index and "& E01 & 3 \\\\" in index
    footer = r"\\fancyfoot\[L\]\{\\footnotesize \\traceflowdocumentcode\{\} \| \S+ 2\.0 \(c4a3b477ecdb\) \| TraceFlow "
    assert re.search(footer, tex)


def test_hierarchy_inline_evidence_index_cites_pages_in_the_report(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    _render_hierarchy(tmp_path, evidence_mode="inline", evidence_index=None)
    tex = fake_latex.tex
    index = tex.split("\\section{Appendix B: Evidence index}")[1]
    assert "& this report & \\pageref{ev:PY-UPLOAD} \\\\" in index
    assert tex.count("\\label{ev:PY-UPLOAD}") == 1


def test_document_set_table_follows_the_cover(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    document_set = parse_document_set(_document_set(), "set.json")
    _render_hierarchy(tmp_path, document_set=document_set, commit="c4a3b477ecdba56e")
    tex = fake_latex.tex
    cover, rest = tex.split("\\section*{Document set}")
    assert "\\tableofcontents" not in cover and "\\tableofcontents" in rest
    assert "Approval of this report covers it and every document listed" in cover
    table = rest.split("\\end{longtable}")[0]
    assert "\\textbf{BXD-\\allowbreak{}VF-\\allowbreak{}019-\\allowbreak{}E01}" in table
    assert "\\hyperref[REQ-VF-T1-001]" in table and "Audit and reporting" in table
    assert "abababababababab\\allowbreak{}" in table
    assert "& 9 & 2 &" in table


def test_document_set_mismatch_fails_before_latex(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    bad = parse_document_set(_document_set(documents=_document_set()["documents"][:1]), "set.json")
    with pytest.raises(DocumentSetError, match="no entry for volumes"):
        _render_hierarchy(tmp_path, document_set=bad)
    assert fake_latex.calls == 0


def test_hierarchy_options_are_checked(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    corpus, results = _hierarchy_corpus(tmp_path)
    report = PdfReport(process_directory(str(corpus), version="2.0"), test_results_dir=str(results))
    with pytest.raises(ValueError, match="always prints list matrices"):
        report.render(structure="hierarchy", matrix_style="tick")
    with pytest.raises(ValueError, match="needs the requirements and the tests"):
        report.render(structure="hierarchy", include_tests=False)
    with pytest.raises(ValueError, match="structure must be one of"):
        report.render(structure="tree")
    with pytest.raises(HierarchyError, match="Tier 1 prefix"):
        report.render(structure="hierarchy", work_dir=str(tmp_path / "w"))


# ------------------------------------------------------------------ files structure: TF-04, TF-06, TF-11


def _render_files(tmp_path: Path, **options: Any) -> None:  # noqa: ANN401
    corpus, results = _hierarchy_corpus(tmp_path)
    report = PdfReport(process_directory(str(corpus), version="2.0"), test_results_dir=str(results))
    report.render(include_design=False, include_supplementary=False, work_dir=str(tmp_path / "work"), **options)


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


# ------------------------------------------------------------------ TF-07 cover and footer, TF-12, reproducibility


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


# ------------------------------------------------------------------ evidence volume footer


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


# ------------------------------------------------------------------ CLI


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"structure": "hierarchy", "render_mode": "risks-only"}, "--structure hierarchy needs the requirements"),
        ({"structure": "hierarchy", "matrix_style": "tick"}, "always prints list matrices"),
        ({"volume_manifest": "m.yml"}, "--volume-manifest needs --render-mode evidence-volume or --structure"),
        ({"risk_register_ref": "BXD-VF-020"}, "--risk-register-ref needs --structure hierarchy"),
        ({"render_mode": "evidence-volume", "volume_manifest": "m.yml", "volume": "E01",
          "document_set_file": "s.json"}, "do not apply to an evidence volume"),
    ],
)
def test_cli_rejects_bad_structure_flags(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kwargs: dict, message: str
) -> None:
    rc = traceflow_main._run(str(tmp_path), "1.0", str(tmp_path / "out.pdf"), **kwargs)
    assert rc == 2
    assert message in capsys.readouterr().err


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


def test_cli_renders_the_hierarchy_report(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    import yaml

    corpus, results = _hierarchy_corpus(tmp_path)
    manifest = tmp_path / "volumes.yml"
    manifest.write_text(yaml.safe_dump(_manifest()))
    index = tmp_path / "all.evidence-index.json"
    write_evidence_index(str(index), _index(tmp_path))
    document_set = tmp_path / "set.json"
    document_set.write_text(json.dumps(_document_set()))
    out = tmp_path / "BXD-VF-019.pdf"
    rc = traceflow_main._run(
        str(corpus), "2.0", str(out), render_mode="requirements-tests-risks", test_results_dir=str(results),
        structure="hierarchy", volume_manifest=str(manifest), evidence_mode="reference", evidence_index=[str(index)],
        document_set_file=str(document_set), document_code="BXD-VF-019", cover_version="Demo 2.0 (commit abc1234)",
        cover_engineer="", commit="c4a3b477ecdb", risk_register_ref="BXD-VF-020", matrix_style="list",
        work_dir=str(tmp_path / "work"), strict_references=True,
    )
    assert rc == 0 and out.read_bytes() == b"%PDF-FAKE"
    assert "\\section*{Document set}" in fake_latex.tex and "\\section{Risk trace}" in fake_latex.tex


def test_cli_reports_document_set_and_hierarchy_errors(tmp_path: Path, fake_latex: _FakeLatex,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    corpus, results = _hierarchy_corpus(tmp_path)
    missing = traceflow_main._run(str(corpus), "2.0", str(tmp_path / "o.pdf"),
                                  document_set_file=str(tmp_path / "none.json"))
    assert missing == 1 and "does not exist" in capsys.readouterr().err
    no_prefix = traceflow_main._run(str(corpus), "2.0", str(tmp_path / "o.pdf"), structure="hierarchy",
                                    test_results_dir=str(results), work_dir=str(tmp_path / "w"))
    assert no_prefix == 1 and "Tier 1 prefix" in capsys.readouterr().err


def test_readme_documents_the_structure_flags() -> None:
    readme = (Path(__file__).parent.parent / "README.md").read_text()
    for flag in ["--structure hierarchy", "--matrix-style list", "--document-set-file", "--cover-engineer",
                 "--commit", "--date-format", "--risk-register-ref", "SOURCE_DATE_EPOCH"]:
        assert flag in readme, flag
