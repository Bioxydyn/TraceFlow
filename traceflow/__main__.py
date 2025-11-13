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
    playwright_dir: Annotated[
        Optional[str],
        Parameter(
            name="--playwright-dir",
            help="Path to the Playwright folder used for `autoplaywright` tests.",
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
) -> int:
    document: Document = process_directory(directory, version=version)
    report: PdfReport = PdfReport(
        document,
        playwright_dir=playwright_dir,
        top_left_logo_path=top_left_logo,
        top_right_logo_path=top_right_logo,
    )
    output_file: bytes = report.render()
    with open(output, "wb") as f:
        f.write(output_file)
    return 0


def main() -> int:
    return app()


if __name__ == "__main__":
    raise SystemExit(main())
