import sys

from traceflow.parser import process_directory, Document
from traceflow.pdf_generator import PdfReport


def main() -> int:
    if len(sys.argv) < 4:
        print("Usage: traceflow <directory> <version> <output>")
        sys.exit(1)

    directory = sys.argv[1]
    version = sys.argv[2]
    output = sys.argv[3]

    document: Document = process_directory(directory, version=version)
    report: PdfReport = PdfReport(document)
    output_file: bytes = report.render()

    with open(output, "wb") as f:
        f.write(output_file)
    return 0
