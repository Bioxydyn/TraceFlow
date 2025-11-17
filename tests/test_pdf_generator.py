import unittest
from traceflow.pdf_generator import PdfReport


class TestPdfGenerator(unittest.TestCase):
    def test_process_text(self) -> None:
        text = "This is some text with a UNIQUE-ID-001 and `code formatted` text too."
        latex = PdfReport.process_text_impl(text, {"UNIQUE-ID-001"})
        expected = r"This is some text with a \hyperref[UNIQUE-ID-001]{UNIQUE-ID-001} and \texttt{code formatted} text too."  # noqa
        self.assertEqual(latex, expected)

    def test_evaluate_risk_rating(self) -> None:
        label, score, colour = PdfReport._evaluate_risk_rating("High", "Medium")
        self.assertEqual(label, "High")
        self.assertEqual(score, 12)
        self.assertTrue(colour.startswith("orange"))

        label, score, colour = PdfReport._evaluate_risk_rating("1", "2")
        self.assertEqual(label, "Low")
        self.assertEqual(score, 2)
