import os
from typing import Annotated, Optional

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
    )
    output_file: bytes = report.render()
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
