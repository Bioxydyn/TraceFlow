"""Evidence rendering: escaping fixes, figure sizing, the record layout, de-duplication,
reference rows, the evidence index and the evidence-volume render.

pdflatex is replaced by a stub that writes a ``.aux`` with a page for every ``ev:`` label,
so the LaTeX the generator emits can be checked without a TeX installation.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct
import textwrap
import zlib
from pathlib import Path
from typing import Any, Optional

import pytest

import traceflow.__main__ as traceflow_main
import traceflow.pdf_generator as pdf_generator
from traceflow.evidence_render import (
    EvidenceIndexError,
    breakable,
    default_index_path,
    evidence_anchor,
    listing_ascii,
    load_evidence_index,
    parse_aux_pages,
    undefined_references,
    write_evidence_index,
)
from traceflow.parser import parse_markdown, process_directory
from traceflow.pdf_generator import PdfReport
from traceflow.volumes import parse_manifest, plan_volumes
from tests.test_volumes import _corpus, _manifest


def _png(width: int = 4, height: int = 3) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\x00\x00\x00" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


def _result_md(evidence_id: str, *, status: str = "passed", requirement: str = "REQ-VOX-UPLOAD-001",
               description: str = "Uploads a scan.", output: bool = False, figure: bool = False) -> str:
    lines = [
        f"# {evidence_id}",
        "",
        "- Kind: `test`",
        f"- Status: `{status}`",
        "- Workflow: `.github/workflows/voxelflow-python.yml`",
        "- Workflow Name: Python package",
        f"- Source: `tests/unit/test_{evidence_id.lower()}.py::TestX::test_y`",
        f"- Description: {description}",
        "- Run ID: `42`",
        "- Run URL: https://github.com/example/repo/actions/runs/42",
        "- Duration: `0.096s`",
        "",
        "## Assertions",
        "",
        f"- ({requirement}) The {evidence_id} check passed",
        "",
        "## Output (last 200 lines)",
        "",
    ]
    if output:
        lines += ["```text", "--------------------------------- Captured Log ---------------------------------",
                  "real output line", "```", ""]
    else:
        lines += ["_No captured output for this entry._", ""]
    lines += ["## Screenshots", ""]
    if figure:
        lines += [f"**Caption:** The {evidence_id} check passed",
                  f"![The {evidence_id} check passed](screenshots/screenshot-001.png)", ""]
    else:
        lines += ["_No screenshots captured for this entry._", ""]
    return "\n".join(lines)


def _write_result(results: Path, evidence_id: str, **kwargs: Any) -> Path:  # noqa: ANN401
    folder = results / evidence_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "result.md").write_text(_result_md(evidence_id, **kwargs))
    if kwargs.get("figure"):
        (folder / "screenshots").mkdir(exist_ok=True)
        (folder / "screenshots" / "screenshot-001.png").write_bytes(_png())
        (folder / "screenshots" / "screenshot-001.json").write_text(
            json.dumps({"caption": f"The {evidence_id} check passed", "requirementIds": ["REQ-VOX-UPLOAD-001"],
                        "checkedAt": "/upload/portal?x=1"})
        )
    return folder


class _FakeLatex:
    """Stands in for pdflatex: writes the .pdf, an .aux with a page per label and a .log."""

    def __init__(self, undefined: Optional[list[str]] = None) -> None:
        self.calls = 0
        self.tex = ""
        self.undefined = undefined or []

    def __call__(self, command: str, **_: object) -> str:
        self.calls += 1
        tex_name = command.split()[-1]
        stem = os.path.splitext(tex_name)[0]
        with open(tex_name, "r", encoding="utf-8") as handle:
            self.tex = handle.read()
        labels = re.findall(r"\\label\{(ev:[^}]+)\}", self.tex)
        with open(stem + ".aux", "w", encoding="utf-8") as handle:
            for page, label in enumerate(labels, start=3):
                handle.write(f"\\newlabel{{{label}}}{{{{}}{{{page}}}{{Title {{nested}}}}{{section*.{page}}}{{}}}}\n")
        with open(stem + ".log", "w", encoding="utf-8") as handle:
            for name in self.undefined:
                handle.write(f"LaTeX Warning: Reference `{name}' on page 4 undefined on input line 12.\n")
        with open(stem + ".pdf", "wb") as handle:
            handle.write(b"%PDF-FAKE")
        return ""


@pytest.fixture
def fake_latex(monkeypatch: pytest.MonkeyPatch) -> _FakeLatex:
    fake = _FakeLatex()
    monkeypatch.setattr(pdf_generator.subprocess, "check_output", fake)
    return fake


def _corpus_with_results(tmp_path: Path, **result_kwargs: Any) -> tuple[Path, Path]:  # noqa: ANN401
    corpus = _corpus(tmp_path / "corpus")
    results = tmp_path / "results"
    for evidence_id in ["PY-UPLOAD", "PW-SHARED-X", "PY-AUDIT-CLAUSE", "PY-CLAUSE-ONLY", "PY-REPORT", "PW-JOURNEY"]:
        requirement = {
            "PY-UPLOAD": "REQ-VOX-UPLOAD-001",
            "PW-SHARED-X": "REQ-VOX-AUDITLOG-001",
            "PY-AUDIT-CLAUSE": "REQ-VOX-AUDITLOG-001",
            "PY-CLAUSE-ONLY": "REQ-VOX-21CFR11-001",
            "PY-REPORT": "REQ-VOX-REPORT-001",
            "PW-JOURNEY": "REQ-VF-T1-001",
        }[evidence_id]
        _write_result(results, evidence_id, requirement=requirement, **result_kwargs)
    return corpus, results


# ------------------------------------------------------------------ helpers


def test_breakable_escapes_and_adds_break_points() -> None:
    assert breakable("a/b_c") == r"a/\allowbreak{}b\_\allowbreak{}c"
    assert breakable("100% #1 ~x {y}") == r"100\% \#1 \textasciitilde{}x \{y\}"
    # A run with no separator still gets a break point every 16 characters.
    assert breakable("0123456789abcdef0123") == r"0123456789abcdef\allowbreak{}0123"
    assert "\\textbackslash{}" in breakable("C:\\Users")


def test_evidence_anchor_keeps_safe_ids_and_slugs_the_rest() -> None:
    assert evidence_anchor("PY-tests-unit-x.py-Test_y") == "ev:PY-tests-unit-x.py-Test_y"
    anchor = evidence_anchor("PW weird#id")
    assert re.fullmatch(r"ev:PW-weird-id-[0-9a-f]{8}", anchor)
    assert evidence_anchor("PW weird#id") != evidence_anchor("PW weird%id")


def test_listing_ascii_maps_symbols_and_drops_control_characters() -> None:
    assert listing_ascii("100%|\u2588\u2588| done \u2014 ok\x1b") == "100%|##| done - ok"
    assert listing_ascii("caf\u00e9 \u4e2d") == "cafe ?"
    assert listing_ascii("\\end{lstlisting}") == "\\end {lstlisting}"


def test_parse_aux_pages_reads_hyperref_labels() -> None:
    aux = (
        "\\relax\n"
        "\\newlabel{ev:PY-A}{{}{12}{Gamma {with} braces}{section*.5}{}}\n"
        "\\newlabel{REQ-1}{{}{3}{Alpha}{section*.2}{}}\n"
        "\\newlabel{ev:PY-B_c.d}{{1.2}{7}{}{figure.1}{}}\n"
    )
    assert parse_aux_pages(aux) == {"ev:PY-A": "12", "ev:PY-B_c.d": "7"}
    assert parse_aux_pages(aux, prefix="REQ-") == {"REQ-1": "3"}


def test_undefined_references_from_the_log() -> None:
    log = (
        "LaTeX Warning: Reference `ev:PY-A' on page 3 undefined on input line 9.\n"
        "LaTeX Warning: Reference `REQ-9' on page 4 undefined on input line 10.\n"
        "LaTeX Warning: Reference `ev:PY-A' on page 5 undefined on input line 11.\n"
    )
    assert undefined_references(log) == ["ev:PY-A", "REQ-9"]
    assert undefined_references(log, "ev:") == ["ev:PY-A"]


def test_evidence_index_round_trip_and_merge(tmp_path: Path) -> None:
    first = tmp_path / "E01.evidence-index.json"
    second = tmp_path / "E02.evidence-index.json"
    write_evidence_index(str(first), {"PY-A": {"volume": "E01", "page": 4, "status": "passed"}})
    write_evidence_index(str(second), {"PY-B": {"volume": "E02", "page": "ii", "status": "failed"}})
    merged = load_evidence_index([str(first), str(second)])
    assert merged == {
        "PY-A": {"volume": "E01", "page": 4, "status": "passed"},
        "PY-B": {"volume": "E02", "page": "ii", "status": "failed"},
    }
    write_evidence_index(str(second), {"PY-A": {"volume": "E02", "page": 1, "status": "passed"}})
    with pytest.raises(EvidenceIndexError, match="listed by two evidence indexes"):
        load_evidence_index([str(first), str(second)])
    second.write_text(json.dumps({"PY-C": {"volume": "E02", "status": "passed"}}))
    with pytest.raises(EvidenceIndexError, match="has no `page`"):
        load_evidence_index([str(second)])
    with pytest.raises(EvidenceIndexError, match="does not exist"):
        load_evidence_index([str(tmp_path / "missing.json")])
    assert default_index_path("/out/BXD-VF-019-E01.pdf") == "/out/BXD-VF-019-E01.evidence-index.json"


# ------------------------------------------------------------------ escaping fixes (default output)


def _report(tmp_path: Path, results: Optional[Path] = None) -> PdfReport:
    corpus = _corpus(tmp_path / "corpus") if not (tmp_path / "corpus").exists() else tmp_path / "corpus"
    document = process_directory(str(corpus), version="1.0")
    return PdfReport(document, test_results_dir=str(results) if results else None)


def test_process_text_escapes_tilde_backslash_and_code_spans() -> None:
    latex = PdfReport.process_text_impl(r"~18 C:\Users `a\b_c/d REQ-1 {x}` REQ-1", {"REQ-1"})
    assert latex.startswith(r"\textasciitilde{}18 C:\textbackslash{}Users ")
    # Inside a code span every special character is escaped and it may break after / and _;
    # a known ID stays a link, as it was before code spans became breakable.
    assert (
        r"\texttt{a\textbackslash{}b\_\allowbreak{}c/\allowbreak{}d \mbox{\hyperref[REQ-1]{\textbf{REQ-1}}} \{x\}}"
        in latex
    )
    # A linked ID outside code sits in an \mbox, so it is never hyphenated.
    assert latex.endswith(r"\mbox{\hyperref[REQ-1]{\textbf{REQ-1}}}")


def test_inline_html_prints_literally_and_br_still_breaks(tmp_path: Path) -> None:
    report = _report(tmp_path)
    latex = report.md_to_latex(parse_markdown("Downloads 21CFR11-<docId>.pdf<br>next"))
    assert "21CFR11-<docId>.pdf" in latex
    assert "\\\\ next" in latex


def test_codespan_nodes_are_escaped_and_breakable(tmp_path: Path) -> None:
    report = _report(tmp_path)
    latex = report.md_to_latex(parse_markdown("Run `tests/unit/test_x.py::Test_y` now"))
    assert r"\texttt{tests/\allowbreak{}unit/\allowbreak{}test\_\allowbreak{}x.\allowbreak{}py:\allowbreak{}" in latex


def test_figures_are_height_capped_without_assertion_source(tmp_path: Path) -> None:
    report = _report(tmp_path)
    latex = report.md_to_latex([{
        "type": "image", "src": "shot.png", "alt": "",
        "_sidecar": {"caption": "Grid shows the scan", "requirementIds": ["REQ-A", "REQ-B"],
                     "assertionSource": "async () => { await expect(x).toBeVisible() }",
                     "checkedAt": "/experiments/7?tab=grid"},
    }])
    assert r"\includegraphics[width=0.7\textwidth,height=0.6\textheight,keepaspectratio]{shot.png}" in latex
    assert "toBeVisible" not in latex
    assert "(evidence for REQ-A, REQ-B)" in latex
    assert r"Checked at \texttt{/\allowbreak{}experiments/\allowbreak{}7?\allowbreak{}tab=\allowbreak{}grid}" in latex
    # Ragged right, so an unbreakable linked ID cannot stretch the line before it.
    assert "\\caption[{Grid shows the scan \\textit{(evidence for REQ-A, REQ-B)}}]{\\raggedright Grid shows" in latex


def test_legacy_block_drops_caption_lines_and_keeps_the_whole_description(tmp_path: Path) -> None:
    results = tmp_path / "results"
    folder = results / "PW-X"
    (folder / "screenshots").mkdir(parents=True)
    (folder / "screenshots" / "screenshot-001.png").write_bytes(_png())
    (folder / "result.md").write_text(textwrap.dedent(
        """\
        # PW-X

        - Kind: `test`
        - Status: `passed`
        - Source: `internal/a-very-long/path/spec.ts::PW-X: does it`
        - Description: Downloads 21CFR11-<docId>.pdf and checks it
        on a second line.

        ## Assertions

        - (REQ-VOX-UPLOAD-001) Receipt 21CFR11-<docId>.pdf downloads

        ## Screenshots

        **Caption:** Receipt 21CFR11-<docId>.pdf downloads
        ![Receipt](screenshots/screenshot-001.png)
        """
    ))
    report = _report(tmp_path, results)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        latex = report._render_autotest_result("PW-X")
    finally:
        os.chdir(cwd)
    assert "Downloads 21CFR11-<docId>.pdf and checks it on a second line." in latex
    assert latex.count("on a second line") == 1
    assert "Caption:" not in latex
    assert "\\begin{figure}[H]" in latex
    # The metadata bullets are ragged right, so a long Source breaks at its separators.
    assert "{\\raggedright\n\\begin{itemize}" in latex


# ------------------------------------------------------------------ record layout


def _render_record(tmp_path: Path, results: Path, evidence_id: str, **options: Any) -> str:  # noqa: ANN401
    report = _report(tmp_path, results)
    report.evidence_layout = "record"
    for name, value in options.items():
        setattr(report, name, value)
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        return report._render_autotest_binding(evidence_id)
    finally:
        os.chdir(cwd)


def test_record_layout_from_result_md(tmp_path: Path) -> None:
    results = tmp_path / "results"
    folder = _write_result(results, "PY-UPLOAD", figure=True)
    latex = _render_record(tmp_path, results, "PY-UPLOAD", evidence_bound_by=True)

    assert "\\phantomsection\\label{ev:PY-UPLOAD}\\hypertarget{ev:PY-UPLOAD}{}" in latex
    assert "\\textbf{Evidence PY-\\allowbreak{}UPLOAD}" in latex
    assert "\\raggedleft \\textbf{\\textcolor{green!50!black}{Passed}}" in latex
    # One provenance line instead of the metadata bullets.
    assert "Test\\enspace\\textbar\\enspace Python package\\enspace\\textbar\\enspace " in latex
    assert "\\href{https://github.com/example/repo/actions/runs/42}{run 42}" in latex
    assert "0.096 s" in latex and "Source \\texttt{tests/\\allowbreak{}unit/" in latex
    assert "\\item Kind" not in latex and "Run URL" not in latex
    assert "\\textbf{Description.} Uploads a scan." in latex
    assert "The PY-UPLOAD check passed" in latex
    # Empty output and placeholder sections are left out; no "Caption:" paragraph.
    assert "Output" not in latex and "No captured output" not in latex and "No screenshots" not in latex
    assert "Caption:" not in latex
    sha = hashlib.sha256((folder / "screenshots" / "screenshot-001.png").read_bytes()).hexdigest()
    assert f"\\mbox{{SHA-256 \\texttt{{{sha[:12]}}}}}" in latex
    assert "\\adjustbox{max width=\\linewidth,max height=0.6\\textheight}" in latex
    assert "Figure~\\ref{evfig:PY-UPLOAD:1}" in latex
    assert "Checked at \\texttt{/\\allowbreak{}upload/" in latex
    assert "\\textbf{Bound by:} \\mbox{\\hyperref[TEST-VOX-UPLOAD-001]" in latex
    assert "\\subsubsection" not in latex  # sub-headings are unnumbered and not in the contents


def test_record_layout_prints_output_only_when_present(tmp_path: Path) -> None:
    results = tmp_path / "results"
    _write_result(results, "PY-UPLOAD", output=True)
    latex = _render_record(tmp_path, results, "PY-UPLOAD")
    assert "\\textbf{Output} {\\footnotesize\\mdseries (1 line)}" in latex
    assert "\\begin{Verbatim}" in latex and "real output line" in latex
    assert "Captured Log" not in latex


def test_record_layout_from_result_json(tmp_path: Path) -> None:
    results = tmp_path / "results"
    folder = results / "PW-JSON"
    (folder / "screenshots").mkdir(parents=True)
    image = _png(8, 6)
    (folder / "screenshots" / "screenshot-001.png").write_bytes(image)
    (folder / "result.json").write_text(json.dumps({
        "schema": 1, "id": "PW-JSON", "kind": "test", "status": "passed",
        "workflow_path": ".github/workflows/frontend-e2e.yml", "workflow_name": "Frontend E2E",
        "run_id": 7, "run_url": "", "head_sha": "", "source": "internal/a.spec.ts::PW-JSON: x",
        "duration_s": 1.5, "description": "Checks *.png and mask-* names", "description_source": "annotation",
        "assertions": [
            {"caption": "Grid row matches", "requirement_ids": ["REQ-VOX-UPLOAD-001"],
             "rows": [["subject", "S-01", "S-01"], ["sha", "a" * 64, "a" * 64]], "checked_at": "/grid", "figure": 0},
            {"caption": "API returns 403", "requirement_ids": [], "rows": [], "checked_at": "/api/x", "figure": None},
        ],
        "figures": [{"path": "screenshots/screenshot-001.png", "caption": "Grid row matches",
                     "sha256": hashlib.sha256(image).hexdigest(), "width": 8, "height": 6}],
        "output": {"lines": ["one", "two"], "total_lines": 40, "truncated": True},
        "jobs": [],
    }))
    latex = _render_record(tmp_path, results, "PW-JSON")
    assert "run 7" in latex and "\\href" not in latex.split("Description")[0]
    assert "Checks *.png and mask-* names" in latex  # no Markdown reinterpretation
    assert "\\textbf{Check} & \\textbf{Expected} & \\textbf{Actual}" in latex
    assert "subject & S-\\allowbreak{}01 & S-\\allowbreak{}01 \\\\" in latex
    assert "aaaaaaaaaaaaaaaa\\allowbreak{}" in latex  # a 64-character hash can break
    assert "--- checked at \\texttt{/\\allowbreak{}api/\\allowbreak{}x}" in latex
    assert "(last 2 of 40 lines)" in latex


def test_missing_fields_warn_in_the_record_layout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    results = tmp_path / "results"
    _write_result(results, "PY-UPLOAD", description="")
    latex = _render_record(tmp_path, results, "PY-UPLOAD")
    assert "Validation gap: this test is missing description." in latex
    assert "WARNING: autotest `PY-UPLOAD` is missing required fields: description" in capsys.readouterr().err


# ------------------------------------------------------------------ de-duplication and the index


def test_dedupe_prints_each_record_once_at_its_home(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    corpus, results = _corpus_with_results(tmp_path)
    report = PdfReport(process_directory(str(corpus), version="1.0"), test_results_dir=str(results))
    index_path = tmp_path / "out" / "report.evidence-index.json"
    report.render(
        include_design=False, include_supplementary=False, evidence_layout="record", dedupe_evidence=True,
        evidence_index_out=str(index_path), document_code="BXD-X", work_dir=str(tmp_path / "work"),
    )
    tex = fake_latex.tex
    for evidence_id in ["PY-UPLOAD", "PW-SHARED-X", "PY-AUDIT-CLAUSE", "PY-CLAUSE-ONLY", "PY-REPORT", "PW-JOURNEY"]:
        assert tex.count(f"\\label{{ev:{evidence_id}}}") == 1, evidence_id
    # PW-SHARED-X asserts REQ-VOX-AUDITLOG-001, so its home is TEST-VOX-AUDITLOG-001 although
    # TEST-VOX-UPLOAD-001 binds it first; that first binding prints a pointer.
    upload_shell = tex.split("\\label{TEST-VOX-UPLOAD-001}")[1].split("\\label{TEST-VOX-21CFR11-001}")[0]
    assert "full record under \\mbox{\\hyperref[TEST-VOX-AUDITLOG-001]" in upload_shell
    assert "p.~\\pageref{ev:PW-SHARED-X}" in upload_shell
    audit_shell = tex.split("\\label{TEST-VOX-AUDITLOG-001}")[1]
    assert "\\label{ev:PW-SHARED-X}" in audit_shell.split("\\label{TEST-VOX-REPORT-001}")[0]
    # The pointer lists only the captions that concern the pointing shell's requirements.
    assert "The PW-SHARED-X check passed" not in upload_shell
    entries = json.loads(index_path.read_text())
    assert set(entries) == {"PY-UPLOAD", "PW-SHARED-X", "PY-AUDIT-CLAUSE", "PY-CLAUSE-ONLY", "PY-REPORT", "PW-JOURNEY"}
    assert entries["PW-SHARED-X"]["volume"] == "BXD-X"
    assert entries["PW-SHARED-X"]["shell"] == "TEST-VOX-AUDITLOG-001"
    assert isinstance(entries["PW-SHARED-X"]["page"], int)
    assert entries["PW-SHARED-X"]["status"] == "passed"
    assert (tmp_path / "work" / "traceflow.tex").is_file()


def test_default_render_keeps_legacy_blocks_and_contents(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    corpus, results = _corpus_with_results(tmp_path)
    report = PdfReport(process_directory(str(corpus), version="1.0"), test_results_dir=str(results))
    report.render(include_design=False, include_supplementary=False, work_dir=str(tmp_path / "work"))
    tex = fake_latex.tex
    assert fake_latex.calls == 3
    assert "\\label{ev:" not in tex and "Evidence PY" not in tex
    assert "\\item Kind: \\texttt{test}" in tex
    assert "tocdepth" not in tex and "secnumdepth" not in tex and "fvextra" not in tex
    assert tex.count("\\subsubsection*{Description}") == 10  # every binding, as today
    assert "\\subsubsection{Output (last 200 lines)}" in tex


def test_undefined_evidence_reference_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    corpus, results = _corpus_with_results(tmp_path)
    monkeypatch.setattr(pdf_generator.subprocess, "check_output", _FakeLatex(undefined=["ev:PY-GONE"]))
    report = PdfReport(process_directory(str(corpus), version="1.0"), test_results_dir=str(results))
    with pytest.raises(ValueError, match="undefined references.*ev:PY-GONE"):
        report.render(include_design=False, include_supplementary=False, dedupe_evidence=True,
                      work_dir=str(tmp_path / "work"))


def test_toc_depth_sets_contents_and_bookmark_depth(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    corpus, results = _corpus_with_results(tmp_path)
    report = PdfReport(process_directory(str(corpus), version="1.0"), test_results_dir=str(results))
    report.render(include_design=False, include_supplementary=False, toc_depth=1, work_dir=str(tmp_path / "w"))
    assert "\\setcounter{tocdepth}{1}" in fake_latex.tex
    assert "\\setcounter{secnumdepth}{0}" in fake_latex.tex
    assert "\\hypersetup{bookmarksdepth=3}" in fake_latex.tex
    # Legacy evidence sub-headings (Output, Screenshots) are unnumbered and out of the contents.
    assert "\\subsubsection*{Output (last 200 lines)}" in fake_latex.tex
    assert "\\subsubsection{Output" not in fake_latex.tex
    with pytest.raises(ValueError, match="toc_depth"):
        report.render(toc_depth=9)


# ------------------------------------------------------------------ reference rows


def _index_for(tmp_path: Path, **entries: dict) -> dict[str, dict]:
    path = tmp_path / "index.json"
    write_evidence_index(str(path), entries)
    return load_evidence_index([str(path)])


def test_reference_mode_prints_one_row_per_binding(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    corpus, results = _corpus_with_results(tmp_path, figure=True)
    ids = ["PY-UPLOAD", "PW-SHARED-X", "PY-AUDIT-CLAUSE", "PY-CLAUSE-ONLY", "PY-REPORT", "PW-JOURNEY"]
    index = _index_for(tmp_path, **{eid: {"volume": "BXD-VF-019-E01", "page": n, "status": "passed"}
                                    for n, eid in enumerate(ids, start=3)})
    report = PdfReport(process_directory(str(corpus), version="1.0"), test_results_dir=str(results))
    report.render(include_design=False, include_supplementary=False, evidence_mode="reference",
                  evidence_index=index, work_dir=str(tmp_path / "work"))
    tex = fake_latex.tex
    assert "\\mbox{BXD-VF-019-E01} \\mbox{p.~3}" in tex
    assert "\\textbf{Evidence} & \\textbf{Status} & \\textbf{Kind} & \\textbf{Asserted} & \\textbf{Record}" in tex
    assert "\\textbullet~The PY-UPLOAD check passed" in tex
    # No figures, no output and no staging in the report.
    assert "\\includegraphics{autotest" not in tex and "adjustbox{max" not in tex
    assert not (tmp_path / "work" / "autotest-results").exists()
    # A record bound twice prints two rows, the second without the header.
    upload_shell = tex.split("\\label{TEST-VOX-UPLOAD-001}")[1].split("\\label{TEST-VOX-21CFR11-001}")[0]
    assert upload_shell.count("\\begin{tabularx}") == 2 and upload_shell.count("\\textbf{Asserted}") == 1
    # PW-SHARED-X asserts only REQ-VOX-AUDITLOG-001; under the upload shell its row still lists
    # what it asserted, with that requirement.
    assert "\\textbullet~The PW-SHARED-X check passed (\\mbox{\\hyperref[REQ-VOX-AUDITLOG-001]" in upload_shell


def test_reference_mode_fails_for_ids_missing_from_the_index(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    corpus, results = _corpus_with_results(tmp_path)
    index = _index_for(tmp_path, **{"PY-UPLOAD": {"volume": "E01", "page": 3, "status": "passed"}})
    report = PdfReport(process_directory(str(corpus), version="1.0"), test_results_dir=str(results))
    with pytest.raises(EvidenceIndexError, match="PW-SHARED-X, PY-AUDIT-CLAUSE, PY-CLAUSE-ONLY"):
        report.render(include_design=False, include_supplementary=False, evidence_mode="reference",
                      evidence_index=index, work_dir=str(tmp_path / "work"))
    assert fake_latex.calls == 0
    with pytest.raises(ValueError, match="needs an evidence index"):
        report.render(evidence_mode="reference")


def test_reference_mode_fails_on_a_stale_status(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    corpus, results = _corpus_with_results(tmp_path)
    ids = ["PY-UPLOAD", "PW-SHARED-X", "PY-AUDIT-CLAUSE", "PY-CLAUSE-ONLY", "PY-REPORT", "PW-JOURNEY"]
    index = _index_for(tmp_path, **{eid: {"volume": "E01", "page": 3, "status": "failed"} for eid in ids})
    report = PdfReport(process_directory(str(corpus), version="1.0"), test_results_dir=str(results))
    with pytest.raises(EvidenceIndexError, match="rebuild the evidence volumes"):
        report.render(include_design=False, include_supplementary=False, evidence_mode="reference",
                      evidence_index=index, work_dir=str(tmp_path / "work"))


# ------------------------------------------------------------------ evidence volumes


def _volume_render(tmp_path: Path, volume: str, **kwargs: Any) -> tuple[PdfReport, dict, Path]:  # noqa: ANN401
    corpus, results = _corpus_with_results(tmp_path, figure=True, output=True)
    document = process_directory(str(corpus), version="2.0")
    plan = plan_volumes(document, parse_manifest(_manifest()))
    report = PdfReport(document, test_results_dir=str(results))
    index_path = tmp_path / "out" / f"{volume}.evidence-index.json"
    _, entries = report.render_evidence_volume(
        plan, volume, evidence_index_out=str(index_path), work_dir=str(tmp_path / f"work-{volume}"), **kwargs
    )
    return report, entries, index_path


def test_evidence_volume_holds_each_home_record_once(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    report, entries, index_path = _volume_render(tmp_path, "BXD-VF-019-E01")
    tex = fake_latex.tex
    # Cover: no signature block, "Evidence volume n of N, part of <report> <version>".
    assert "Evidence volume 1 of 2, part of BXD-VF-019 2.0" in tex
    assert "Validation pack result approved by" not in tex and "Lead Software Engineer" not in tex
    assert "Approved as part of \\mbox{BXD-VF-019 2.0}; no separate signature." in tex
    assert "3 of 3 records in this volume passed." in tex
    assert "Traceability Evidence Volume E01: Ingestion" in tex
    assert "\\setcounter{tocdepth}{2}" in tex
    # How-to-read page and record index.
    assert "\\section*{How to read this volume}" in tex
    assert "\\section*{Record index}" in tex and "\\begin{longtable}" in tex
    assert "\\hyperref[ev:PY-UPLOAD]{PY-\\allowbreak{}UPLOAD} &" in tex and "\\pageref{ev:PY-UPLOAD}" in tex
    # Body: Tier 1, then shell headings (ID, title and requirements only); journey shell first.
    assert "\\section*{REQ-VF-T1-001: Ingest}" in tex
    assert tex.index("TEST-VF-T1-001: Shell") < tex.index("TEST-VOX-UPLOAD-001: Shell")
    assert "Verifies: REQ-VOX-UPLOAD-001." in tex
    # Home records once each; PW-SHARED-X is homed here (manifest order breaks the tie).
    for evidence_id in ["PY-UPLOAD", "PW-SHARED-X", "PW-JOURNEY"]:
        assert tex.count(f"\\label{{ev:{evidence_id}}}") == 1
    assert "\\label{ev:PY-AUDIT-CLAUSE}" not in tex
    # The second binding of PY-UPLOAD (journey shell first, so it is the first binding) points to
    # the functional shell's copy.
    assert "full record under \\mbox{\\hyperref[vshell:TEST-VOX-UPLOAD-001]" in tex
    # IDs are bold text, not links to sections the volume does not contain.
    assert "\\hyperref[REQ-" not in tex and "\\hyperref[TEST-" not in tex
    # Bound by names shells in other volumes with their volume code.
    assert "\\mbox{\\textbf{TEST-VOX-AUDITLOG-001}} (\\mbox{BXD-VF-019-E02})" in tex
    assert set(entries) == {"PY-UPLOAD", "PW-SHARED-X", "PW-JOURNEY"}
    assert json.loads(index_path.read_text()) == entries
    assert entries["PY-UPLOAD"]["volume"] == "BXD-VF-019-E01"
    assert entries["PY-UPLOAD"]["shell"] == "TEST-VOX-UPLOAD-001"
    assert entries["PY-UPLOAD"]["figures"] == 1
    # The evidence options are restored after the volume.
    assert report.evidence_layout == "legacy" and not report.dedupe_evidence and report._volume is None


def test_evidence_volume_points_to_records_in_other_volumes(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    other = {"PW-SHARED-X": {"volume": "BXD-VF-019-E01", "page": 9, "status": "passed"}}
    _volume_render(tmp_path, "audit", evidence_index=other)
    tex = fake_latex.tex
    assert "Evidence volume 2 of 2" in tex
    assert "\\section*{REQ-VF-T1-002: Audit}" in tex and "\\section*{REQ-VF-T1-003: Report}" in tex
    assert "\\label{ev:PW-SHARED-X}" not in tex
    assert "full record in \\mbox{BXD-VF-019-E01} under \\mbox{\\textbf{TEST-VOX-UPLOAD-001}}, p.~9" in tex
    for evidence_id in ["PY-AUDIT-CLAUSE", "PY-CLAUSE-ONLY", "PY-REPORT"]:
        assert tex.count(f"\\label{{ev:{evidence_id}}}") == 1


def test_evidence_volume_index_needs_every_label_placed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    class _NoAux(_FakeLatex):
        def __call__(self, command: str, **kwargs: object) -> str:
            super().__call__(command, **kwargs)
            os.remove(os.path.splitext(command.split()[-1])[0] + ".aux")
            return ""

    monkeypatch.setattr(pdf_generator.subprocess, "check_output", _NoAux())
    with pytest.raises(ValueError, match="placed no label for evidence"):
        _volume_render(tmp_path, "BXD-VF-019-E01")


# ------------------------------------------------------------------ CLI


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"render_mode": "evidence-volume"}, "needs --volume-manifest and --volume"),
        ({"render_mode": "all", "volume": "E01"}, "--volume needs --render-mode evidence-volume"),
        ({"render_mode": "all", "evidence_mode": "reference"}, "needs at least one --evidence-index"),
        ({"render_mode": "all", "evidence_index": ["a.json"]}, "--evidence-index needs --evidence-mode reference"),
        ({"render_mode": "all", "toc_depth": 7}, "between 0 and 5"),
        ({"render_mode": "evidence-volume", "volume_manifest": "m.yml", "volume": "E01",
          "evidence_mode": "reference"}, "does not apply to an evidence volume"),
    ],
)
def test_cli_rejects_bad_evidence_flag_combinations(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], kwargs: dict, message: str
) -> None:
    rc = traceflow_main._run(str(tmp_path), "1.0", str(tmp_path / "out.pdf"), **kwargs)
    assert rc == 2
    assert message in capsys.readouterr().err


def test_cli_passes_evidence_options_to_render(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class _Stub:
        def __init__(self, document: object, **_: object) -> None:
            pass

        def render(self, **kwargs: object) -> bytes:
            captured.update(kwargs)
            return b"%PDF-FAKE"

    index = tmp_path / "E01.evidence-index.json"
    write_evidence_index(str(index), {"PY-A": {"volume": "E01", "page": 1, "status": "passed"}})
    monkeypatch.setattr(traceflow_main, "process_directory", lambda directory, version: object())
    monkeypatch.setattr(traceflow_main, "PdfReport", _Stub)
    rc = traceflow_main._run(
        str(tmp_path), "1.0", str(tmp_path / "out.pdf"), evidence_mode="reference", evidence_index=[str(index)],
        toc_depth=1, work_dir=str(tmp_path / "w"), strict_references=True,
    )
    assert rc == 0
    assert captured["evidence_mode"] == "reference"
    assert captured["evidence_index"] == {"PY-A": {"volume": "E01", "page": 1, "status": "passed"}}
    assert (captured["toc_depth"], captured["work_dir"]) == (1, str(tmp_path / "w"))
    assert captured["strict_references"] is True
    assert (captured["evidence_layout"], captured["dedupe_evidence"]) == ("legacy", False)


def test_cli_renders_an_evidence_volume(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    import yaml

    corpus, results = _corpus_with_results(tmp_path)
    manifest = tmp_path / "volumes.yml"
    manifest.write_text(yaml.safe_dump(_manifest()))
    output = tmp_path / "out" / "BXD-VF-019-E02.pdf"
    output.parent.mkdir()
    rc = traceflow_main._run(
        str(corpus), "2.0", str(output), render_mode="evidence-volume", volume_manifest=str(manifest),
        volume="audit", test_results_dir=str(results), work_dir=str(tmp_path / "work"),
    )
    assert rc == 0
    assert output.read_bytes() == b"%PDF-FAKE"
    entries = json.loads((tmp_path / "out" / "BXD-VF-019-E02.evidence-index.json").read_text())
    assert set(entries) == {"PY-AUDIT-CLAUSE", "PY-CLAUSE-ONLY", "PY-REPORT"}
    assert "\\section*{How to read this volume}" in fake_latex.tex


def test_cli_volume_errors_exit_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    import yaml

    corpus, results = _corpus_with_results(tmp_path)
    manifest = tmp_path / "volumes.yml"
    manifest.write_text(yaml.safe_dump(_manifest()))
    rc = traceflow_main._run(
        str(corpus), "2.0", str(tmp_path / "x.pdf"), render_mode="evidence-volume", volume_manifest=str(manifest),
        volume="E09", test_results_dir=str(results),
    )
    assert rc == 1
    assert "No volume `E09`" in capsys.readouterr().err


def test_cli_volume_refuses_another_documents_code(tmp_path: Path, fake_latex: _FakeLatex,
                                                   capsys: pytest.CaptureFixture[str]) -> None:
    import yaml

    corpus, results = _corpus_with_results(tmp_path)
    manifest = tmp_path / "volumes.yml"
    manifest.write_text(yaml.safe_dump(_manifest()))
    common: dict[str, Any] = {
        "render_mode": "evidence-volume", "volume_manifest": str(manifest), "volume": "audit",
        "test_results_dir": str(results), "work_dir": str(tmp_path / "work"),
    }
    # The report's code (flags copied from the report's command line) would mislabel the volume.
    rc = traceflow_main._run(str(corpus), "2.0", str(tmp_path / "x.pdf"), document_code="BXD-VF-019", **common)
    assert rc == 1
    assert "--document-code `BXD-VF-019` is not the code of volume `BXD-VF-019-E02`" in capsys.readouterr().err
    assert fake_latex.calls == 0
    # The volume's own code is accepted.
    assert traceflow_main._run(str(corpus), "2.0", str(tmp_path / "x.pdf"), document_code="BXD-VF-019-E02",
                               **common) == 0


# ------------------------------------------------------------------ end-to-end review fixes


def test_code_span_keeps_links_to_known_ids(tmp_path: Path) -> None:
    report = _report(tmp_path)
    report.unique_ids = {"REQ-VF-T1-009"}
    latex = report.md_to_latex(parse_markdown("children of `REQ-VF-T1-009: Reporting, live` and `PW-X_1`."))
    # The ID keeps its link (as before code spans became breakable); the rest is escaped and breakable.
    assert r"\texttt{\mbox{\hyperref[REQ-VF-T1-009]{\textbf{REQ-VF-T1-009}}}:\allowbreak{} Reporting," in latex
    assert r"\texttt{PW-\allowbreak{}X\_\allowbreak{}1}" in latex
    # With no known IDs (an evidence volume) a code span is exactly the breakable form.
    assert PdfReport.code_span_latex("REQ-VF-T1-009: a/b", set()) == (
        r"\texttt{REQ-\allowbreak{}VF-\allowbreak{}T1-\allowbreak{}009:\allowbreak{} a/\allowbreak{}b}"
    )


def test_heading_with_linked_ids_has_a_plain_bookmark(tmp_path: Path) -> None:
    report = _report(tmp_path)
    report.unique_ids = {"REQ-VOX-UPLOAD-001"}
    heading = report.heading_text("REQ-VOX-UPLOAD-001: Upload a scan")
    # The printed heading is unchanged; the PDF bookmark reads "REQ-VOX-UPLOAD-001: Upload a scan",
    # not "[REQ-VOX-UPLOAD-001]REQ-VOX-UPLOAD-001: Upload a scan".
    assert heading == (
        r"\texorpdfstring{\mbox{\hyperref[REQ-VOX-UPLOAD-001]{\textbf{REQ-VOX-UPLOAD-001}}}: Upload a scan}"
        r"{REQ-VOX-UPLOAD-001: Upload a scan}"
    )
    assert report.heading_text("Upload Requirements") == "Upload Requirements"
    latex = report.md_to_latex(parse_markdown("## REQ-VOX-UPLOAD-001: Upload a scan"))
    assert latex.startswith(r"\subsection{\texorpdfstring{\mbox{\hyperref[REQ-VOX-UPLOAD-001]")


def test_default_render_headings_get_plain_bookmarks(tmp_path: Path, fake_latex: _FakeLatex) -> None:
    corpus, results = _corpus_with_results(tmp_path)
    report = PdfReport(process_directory(str(corpus), version="1.0"), test_results_dir=str(results))
    report.render(include_design=False, include_supplementary=False, work_dir=str(tmp_path / "work"))
    assert r"\subsection{\texorpdfstring{\mbox{\hyperref[REQ-VOX-UPLOAD-001]{\textbf{REQ-VOX-UPLOAD-001}}}: " in (
        fake_latex.tex
    )
    assert re.search(r"\\subsection\{\\mbox\{\\hyperref", fake_latex.tex) is None


def test_record_figure_caption_is_ragged_right(tmp_path: Path) -> None:
    results = tmp_path / "results"
    _write_result(results, "PY-UPLOAD", figure=True)
    latex = _render_record(tmp_path, results, "PY-UPLOAD")
    caption = re.search(r"\\caption\[\{(.*?)\}\]\{\\raggedright (.*?)\}\\label\{evfig:PY-UPLOAD:1\}", latex)
    # A justified caption stretched its first line when the unbreakable SHA-256 did not fit.
    assert caption is not None and caption.group(1) == caption.group(2)
    assert "SHA-256" in caption.group(1)


def test_record_provenance_says_when_a_record_is_not_from_ci(tmp_path: Path) -> None:
    results = tmp_path / "results"
    folder = _write_result(results, "PY-UPLOAD")
    md = (folder / "result.md").read_text()
    md = md.replace("- Run ID: `42`", "- Run ID: `0`").replace(
        "- Run URL: https://github.com/example/repo/actions/runs/42", "- Run URL: <unavailable>"
    )
    (folder / "result.md").write_text(md)
    latex = _render_record(tmp_path, results, "PY-UPLOAD")
    assert "\\textit{not a CI run}" in latex and "run 0" not in latex
