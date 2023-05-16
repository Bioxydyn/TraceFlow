from traceflow.parser import process_directory, Document
from traceflow.pdf_generator import PdfReport

if __name__ == "__main__":
    document: Document = process_directory("examples")

    report: PdfReport = PdfReport(document)
    report.render()


#     {
#         "children": [
#             {
#                 "attrs": {
#                     "url": "./traceflow-logo.png"
#                 },
#                 "children": [
#                     {
#                         "raw": "MRI Sample",
#                         "type": "text"
#                     }
#                 ],
#                 "type": "image"
#             }
#         ],
#         "type": "paragraph"
#     },
#     {
#         "type": "blank_line"
#     }
# ]

