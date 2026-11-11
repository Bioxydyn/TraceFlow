import os
import sys
from typing import Annotated, Optional, Literal

from cyclopts import App, Parameter

from traceflow.evidence_render import EvidenceIndexError, default_index_path, load_evidence_index
from traceflow.parser import process_directory, Document
from traceflow.pdf_generator import PdfReport
from traceflow.report_structure import check_commit
from traceflow.version import __version__
from traceflow.volumes import (
    ManifestError,
    VolumePartitionError,
    check_volumes,
    format_report,
    load_manifest,
    plan_volumes,
)

app = App(name="traceflow", version=__version__)


@app.default
def _run(
    directory: str,
    version: Optional[str] = None,
    output: Optional[str] = None,
    render_mode: Annotated[
        Literal["all", "requirements-tests-risks", "risks-only", "evidence-volume"],
        Parameter(
            name="--render-mode",
            help="Rendering mode: all sections, requirements/tests/risks only, risks only, or one "
            "evidence volume (with --volume-manifest and --volume).",
        ),
    ] = "all",
    test_results_dir: Annotated[
        Optional[str],
        Parameter(
            name="--test-results-dir",
            help="Path to a folder containing test result subfolders used by `autotest` blocks.",
        ),
    ] = None,
    top_left_logo: Annotated[
        Optional[str],
        Parameter(
            name=("--top-left-logo", "--traceflow-logo"),
            help="Path to the top-left logo (header).",
        ),
    ] = None,
    top_right_logo: Annotated[
        Optional[str],
        Parameter(
            name=("--top-right-logo", "--voxelflow-logo"),
            help="Path to the top-right logo (footer).",
        ),
    ] = None,
    report_title: Annotated[
        Optional[str],
        Parameter(
            name="--report-title",
            help="Optional title displayed on the report title page.",
        ),
    ] = None,
    document_code: Annotated[
        Optional[str],
        Parameter(
            name="--document-code",
            help="Optional document code shown in the report (for example MCD-1CL-019).",
        ),
    ] = None,
    document_type: Annotated[
        Optional[str],
        Parameter(
            name="--document-type",
            help="Optional document type shown in the report metadata table.",
        ),
    ] = None,
    skip_toc: Annotated[
        bool,
        Parameter(
            name="--skip-toc",
            help="Skip rendering the table of contents.",
        ),
    ] = False,
    skip_front_page: Annotated[
        bool,
        Parameter(
            name="--skip-front-page",
            help="Skip rendering the cover-page version/signature sections.",
        ),
    ] = False,
    cover_signature_boxes: Annotated[
        bool,
        Parameter(
            name="--cover-signature-boxes",
            help="Use boxed signature placeholders on the cover page.",
        ),
    ] = False,
    traceability_a3: Annotated[
        bool,
        Parameter(
            name="--traceability-a3",
            help="Render traceability-matrix pages using A3 landscape layout.",
        ),
    ] = False,
    cover_no_tester: Annotated[
        bool,
        Parameter(
            name="--cover-no-tester",
            help="Omit the 'Tester' row from the cover metadata table (for fully automated packs).",
        ),
    ] = False,
    cover_test_date: Annotated[
        Optional[str],
        Parameter(
            name="--cover-test-date",
            help="Value pre-filled into the cover 'Test Date' cell (default: blank).",
        ),
    ] = None,
    cover_result: Annotated[
        Optional[str],
        Parameter(
            name="--cover-result",
            help="Value pre-filled into the cover 'Result' cell (default: blank).",
        ),
    ] = None,
    cover_approved_usage: Annotated[
        Optional[str],
        Parameter(
            name="--cover-approved-usage",
            help="Value pre-filled into the cover 'Approved Usage' cell (default: blank).",
        ),
    ] = None,
    cover_single_signoff: Annotated[
        bool,
        Parameter(
            name="--cover-single-signoff",
            help="Render only the 'Validation pack result approved by' sign-off block, "
            "omitting the 'Manual tests carried out by' block (for fully automated packs).",
        ),
    ] = False,
    no_right_logo: Annotated[
        bool,
        Parameter(
            name="--hide-right-logo",
            help="Render no logo in the header's right slot (a single left-hand logo). "
            "Without this the right slot falls back to the bundled logo.",
        ),
    ] = False,
    manualtest_comment_height: Annotated[
        str,
        Parameter(
            name="--manualtest-comment-height",
            help="Height of the fillable Comments box under each manual test (default 1.2cm).",
        ),
    ] = "1.2cm",
    individual_pdfs: Annotated[
        bool,
        Parameter(
            name="--individual-pdfs",
            help="Also emit per-section PDFs alongside the combined validation pack.",
        ),
    ] = False,
    evidence_layout: Annotated[
        Literal["legacy", "record"],
        Parameter(
            name="--evidence-layout",
            help="How an evidence record prints: 'legacy' (default, today's blocks) or 'record' "
            "(an 'Evidence <id>' header with status, one provenance line, description, assertions "
            "with their rows, output when non-empty, figures with SHA-256).",
        ),
    ] = "legacy",
    evidence_mode: Annotated[
        Literal["inline", "reference"],
        Parameter(
            name="--evidence-mode",
            help="'inline' (default): records print where bound. 'reference': each binding prints "
            "one row (ID | status | kind | assertion captions | <volume> p.<page>) from --evidence-index.",
        ),
    ] = "inline",
    evidence_index: Annotated[
        Optional[list[str]],
        Parameter(
            name="--evidence-index",
            help="An evidence index JSON written by a volume render (repeat for each volume). "
            "Needed by --evidence-mode reference; in an evidence-volume render it adds page numbers "
            "to pointers into other volumes.",
        ),
    ] = None,
    dedupe_evidence: Annotated[
        bool,
        Parameter(
            name="--dedupe-evidence",
            help="Print each evidence record in full once, at its home binding, and a one-line "
            "pointer (with its page) at every other binding.",
        ),
    ] = False,
    evidence_bound_by: Annotated[
        bool,
        Parameter(
            name="--evidence-bound-by",
            help="End each record-layout evidence record with its 'Bound by' list.",
        ),
    ] = False,
    evidence_index_out: Annotated[
        Optional[str],
        Parameter(
            name="--evidence-index-out",
            help="Write {id: {volume, page, status, ...}} for every record printed in full, read "
            "from the final LaTeX pass. An evidence-volume render writes "
            "<OUTPUT stem>.evidence-index.json when this is not given.",
        ),
    ] = None,
    volume_manifest: Annotated[
        Optional[str],
        Parameter(
            name="--volume-manifest",
            help="Volume manifest (YAML) for --render-mode evidence-volume.",
        ),
    ] = None,
    volume: Annotated[
        Optional[str],
        Parameter(
            name="--volume",
            help="Code or slug of the volume to render (--render-mode evidence-volume).",
        ),
    ] = None,
    cover_version: Annotated[
        Optional[str],
        Parameter(
            name="--cover-version",
            help="'Version under test' on the cover, for example 'VoxelFlow 3.0.5 (commit abc123def456)'. "
            "Not given: the row stays blank (an evidence volume prints '<name> <VERSION>'); given as '': "
            "the row is left out.",
        ),
    ] = None,
    cover_engineer: Annotated[
        Optional[str],
        Parameter(
            name="--cover-engineer",
            help="'Engineer' on the cover. Not given: the row stays blank; given as '': the row is left out.",
        ),
    ] = None,
    commit: Annotated[
        Optional[str],
        Parameter(
            name="--commit",
            help="The product commit under test (7-40 hex). The footer then reads "
            "'<code> | <name> <VERSION> (<commit12>) | TraceFlow <version>'.",
        ),
    ] = None,
    date_format: Annotated[
        Optional[str],
        Parameter(
            name="--date-format",
            help="strftime format of the generation date on the cover (for example '%d %B %Y'). "
            "Evidence volumes default to '%d %B %Y'; otherwise LaTeX's \\today. "
            "SOURCE_DATE_EPOCH, when set, fixes the date and the PDF's ID and timestamps.",
        ),
    ] = None,
    toc_depth: Annotated[
        Optional[int],
        Parameter(
            name="--toc-depth",
            help="Contents depth (0-5). Also unnumbers headings; PDF bookmarks keep depth 3. "
            "Default: today's contents (an evidence volume defaults to 2).",
        ),
    ] = None,
    work_dir: Annotated[
        Optional[str],
        Parameter(
            name="--work-dir",
            help="Build folder for the LaTeX run (default ./report). Give each concurrent render its own.",
        ),
    ] = None,
    strict_references: Annotated[
        bool,
        Parameter(
            name="--strict-references",
            help="Fail if the final LaTeX pass reports any undefined reference. Undefined references "
            "to evidence records always fail.",
        ),
    ] = False,
    check_volumes_manifest: Annotated[
        Optional[str],
        Parameter(
            name="--check-volumes",
            help="Parse the whole directory, partition it with this volume manifest (YAML) and print "
            "the ID-to-volume map as JSON on stdout instead of rendering. Exits 1 if the partition "
            "fails (the JSON then lists the errors). VERSION and OUTPUT are not needed.",
        ),
    ] = None,
) -> int:
    if check_volumes_manifest is not None:
        report, exit_code = check_volumes(directory, check_volumes_manifest, version=version or "")
        sys.stdout.write(format_report(report))
        for error in report.get("errors", []):
            sys.stderr.write(f"ERROR: {error}\n")
        return exit_code
    if version is None or output is None:
        sys.stderr.write("ERROR: traceflow needs DIRECTORY VERSION OUTPUT to render a PDF.\n")
        return 2
    usage_error = _evidence_usage_error(
        render_mode=render_mode,
        evidence_mode=evidence_mode,
        evidence_index=evidence_index,
        volume_manifest=volume_manifest,
        volume=volume,
        toc_depth=toc_depth,
    )
    if not usage_error:
        try:
            check_commit(commit)
        except ValueError as exc:
            usage_error = str(exc)
    if usage_error:
        sys.stderr.write(f"ERROR: {usage_error}\n")
        return 2
    try:
        index = load_evidence_index(evidence_index) if evidence_index else None
    except EvidenceIndexError as exc:
        sys.stderr.write(f"ERROR: {exc}\n")
        return 1
    document: Document = process_directory(directory, version=version)
    report: PdfReport = PdfReport(
        document,
        test_results_dir=test_results_dir,
        top_left_logo_path=top_left_logo,
        top_right_logo_path=top_right_logo,
        manualtest_comment_height=manualtest_comment_height,
    )
    if render_mode == "evidence-volume":
        assert volume_manifest is not None and volume is not None
        try:
            plan = plan_volumes(document, load_manifest(volume_manifest))
            pdf_bytes, _ = report.render_evidence_volume(
                plan,
                volume,
                report_title=report_title,
                document_code=document_code,
                document_type=document_type,
                include_toc=not skip_toc,
                toc_depth=2 if toc_depth is None else toc_depth,
                no_right_logo=no_right_logo,
                cover_version=cover_version,
                evidence_index=index,
                evidence_index_out=evidence_index_out or default_index_path(output),
                work_dir=work_dir,
                strict_references=strict_references,
                commit=commit,
                date_format=date_format,
            )
        except (ManifestError, VolumePartitionError) as exc:
            for error in exc.errors:
                sys.stderr.write(f"ERROR: {error}\n")
            return 1
        except KeyError as exc:
            sys.stderr.write(f"ERROR: {exc.args[0]}\n")
            return 1
        with open(output, "wb") as f:
            f.write(pdf_bytes)
        return 0

    include_design = True
    include_supplementary = True
    include_requirements = True
    include_tests = True
    include_risks = True
    if render_mode == "requirements-tests-risks":
        include_design = False
        include_supplementary = False
    elif render_mode == "risks-only":
        include_design = False
        include_supplementary = False
        include_requirements = False
        include_tests = False

    output_file: bytes = report.render(
        include_design=include_design,
        include_supplementary=include_supplementary,
        include_risks=include_risks,
        include_requirements=include_requirements,
        include_tests=include_tests,
        report_title=report_title,
        include_cover_page=not skip_front_page,
        include_toc=not skip_toc,
        cover_signature_boxes=cover_signature_boxes,
        cover_no_tester=cover_no_tester,
        cover_test_date=cover_test_date,
        cover_result=cover_result,
        cover_approved_usage=cover_approved_usage,
        cover_single_signoff=cover_single_signoff,
        no_right_logo=no_right_logo,
        document_code=document_code,
        document_type=document_type,
        traceability_a3=traceability_a3,
        evidence_layout=evidence_layout,
        evidence_mode=evidence_mode,
        dedupe_evidence=dedupe_evidence,
        evidence_bound_by=evidence_bound_by,
        evidence_index=index,
        evidence_index_out=evidence_index_out,
        toc_depth=toc_depth,
        work_dir=work_dir,
        strict_references=strict_references,
        cover_version=cover_version,
        cover_engineer=cover_engineer,
        commit=commit,
        date_format=date_format,
    )
    with open(output, "wb") as f:
        f.write(output_file)

    if individual_pdfs:
        base_output, extension = os.path.splitext(output)
        for filename in report.render_individual_pdfs(base_output, extension=extension or ".pdf"):
            print(f"Wrote individual PDF: {filename}")
    return 0


def _evidence_usage_error(
    *,
    render_mode: str,
    evidence_mode: str,
    evidence_index: Optional[list[str]],
    volume_manifest: Optional[str],
    volume: Optional[str],
    toc_depth: Optional[int],
) -> str:
    """The first invalid combination of evidence flags, or ``""``."""
    volume_mode = render_mode == "evidence-volume"
    if volume_mode and (not volume_manifest or not volume):
        return "--render-mode evidence-volume needs --volume-manifest and --volume."
    if volume_mode and evidence_mode == "reference":
        return "--evidence-mode reference does not apply to an evidence volume."
    if not volume_mode and (volume_manifest or volume):
        return "--volume-manifest and --volume need --render-mode evidence-volume."
    if not volume_mode and evidence_mode == "reference" and not evidence_index:
        return "--evidence-mode reference needs at least one --evidence-index."
    if not volume_mode and evidence_index and evidence_mode != "reference":
        return "--evidence-index needs --evidence-mode reference (or --render-mode evidence-volume)."
    if toc_depth is not None and not 0 <= toc_depth <= 5:
        return "--toc-depth must be between 0 and 5."
    return ""


def main() -> int:
    return app()


if __name__ == "__main__":
    raise SystemExit(main())
