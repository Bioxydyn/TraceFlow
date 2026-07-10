import os
from typing import Annotated, Optional, Literal

from cyclopts import App, Parameter

from traceflow.parser import process_directory, Document
from traceflow.pdf_generator import PdfReport
from traceflow.version import __version__

app = App(name="traceflow", version=__version__)


@app.default
def _run(
    directory: str,
    version: str,
    output: str,
    render_mode: Annotated[
        Literal["all", "requirements-tests-risks", "risks-only"],
        Parameter(
            name="--render-mode",
            help="Rendering mode: all sections, requirements/tests/risks only, or risks only.",
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
) -> int:
    document: Document = process_directory(directory, version=version)
    report: PdfReport = PdfReport(
        document,
        test_results_dir=test_results_dir,
        top_left_logo_path=top_left_logo,
        top_right_logo_path=top_right_logo,
        manualtest_comment_height=manualtest_comment_height,
    )
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
        document_code=document_code,
        document_type=document_type,
        traceability_a3=traceability_a3,
    )
    with open(output, "wb") as f:
        f.write(output_file)

    if individual_pdfs:
        base_output, extension = os.path.splitext(output)
        for filename in report.render_individual_pdfs(base_output, extension=extension or ".pdf"):
            print(f"Wrote individual PDF: {filename}")
    return 0


def main() -> int:
    return app()


if __name__ == "__main__":
    raise SystemExit(main())
