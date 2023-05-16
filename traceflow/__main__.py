import sys

from traceflow.parser import process_directory, Document
from traceflow.pdf_generator import PdfReport

def main():
    # First command line arg is the directory to process
    # Second is the output file path

    if len(sys.argv) < 3:
        print("Usage: traceflow <directory> <output>")
        sys.exit(1)

    directory = sys.argv[1]
    output = sys.argv[2]

    document: Document = process_directory(directory)
    report: PdfReport = PdfReport(document)
    output_file: bytes = report.render()

    with open(output, "wb") as f:
        f.write(output_file)
