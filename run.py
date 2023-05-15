from traceflow.parser import process_directory, Document
from traceflow.pdf_generator import PdfReport

if __name__ == "__main__":
    document: Document = process_directory("examples")

    report: PdfReport = PdfReport(document)
    report.render()
