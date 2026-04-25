import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from typing import Optional

from traceflow.pdf_generator import PdfReport
from traceflow.parser import (
    Document,
    MarkdownDocument,
    Requirement,
    RequirementDocument,
    Risk,
    RiskDocument,
    Test,
    TestDocument,
    extract_autotest_result,
    parse_markdown,
)


class TestPdfGenerator(unittest.TestCase):

    def _build_document(self) -> Document:
        requirement = Requirement(req_id="REQ-001", content=[], title="Login", test_ids=["TEST-001"])
        requirement_doc = RequirementDocument(
            title="Requirements",
            generic_content=[],
            filename="req.md",
            items=[requirement],
        )

        test_item = Test(test_id="TEST-001", content=[], title="Login", req_ids=["REQ-001"])
        test_doc = TestDocument(
            title="Tests",
            generic_content=[],
            filename="tests.md",
            items=[test_item],
        )

        risk_item = Risk(
            risk_id="RISK-001",
            content=[],
            title="Missing controls",
            attributes={},
            requirement_refs=["REQ-001"],
            test_refs=["TEST-001"],
        )
        risk_doc = RiskDocument(
            title="Risk Register",
            generic_content=[],
            filename="risk.md",
            items=[risk_item],
        )

        design_doc = MarkdownDocument(title="Design", filename="design.md", content=[], category="design")
        supplementary_doc = MarkdownDocument(title="IUS", filename="ius.md", content=[], category="general")

        return Document(
            requirements=[requirement_doc],
            tests=[test_doc],
            risks=[risk_doc],
            design_documents=[design_doc],
            supplementary_documents=[supplementary_doc],
            name="TraceFlow",
            input_dir=".",
            version="1.0.0",
        )

    def test_process_text(self) -> None:
        text = "This is some text with a UNIQUE-ID-001 and `code formatted` text too."
        latex = PdfReport.process_text_impl(text, {"UNIQUE-ID-001"})
        expected = (
            r"This is some text with a \hyperref[UNIQUE-ID-001]{\textbf{UNIQUE-ID-001}}"
            r" and \texttt{code formatted} text too."
        )
        self.assertEqual(latex, expected)

    def test_evaluate_risk_rating(self) -> None:
        label, score, colour = PdfReport._evaluate_risk_rating("High", "Medium")
        self.assertEqual(label, "High")
        self.assertEqual(score, 12)
        self.assertTrue(colour.startswith("orange"))

        label, score, colour = PdfReport._evaluate_risk_rating("1", "2")
        self.assertEqual(label, "Low")
        self.assertEqual(score, 2)

    def test_individual_report_specs_respects_available_sections(self) -> None:
        report = PdfReport(self._build_document())
        specs = report._individual_report_specs()
        suffixes = [spec.suffix for spec in specs]
        self.assertEqual(["design", "docs", "risks", "requirements", "tests"], suffixes)

        requirements_spec = next(spec for spec in specs if spec.suffix == "requirements")
        self.assertTrue(requirements_spec.flags["include_requirements"])
        self.assertFalse(requirements_spec.flags["include_tests"])

    def test_render_individual_pdfs_skips_cover_page(self) -> None:
        report = PdfReport(self._build_document())
        cover_flags: list[Optional[bool]] = []

        def fake_render(self, **kwargs):  # type: ignore[override]
            cover_flags.append(kwargs.get("include_cover_page"))
            return b"%PDF-FAKE"

        report.render = fake_render.__get__(report, PdfReport)
        with tempfile.TemporaryDirectory() as tmpdir:
            base = Path(tmpdir) / "output"
            outputs = report.render_individual_pdfs(str(base))
            for output in outputs:
                self.assertTrue(Path(output).exists())
        self.assertTrue(cover_flags)
        self.assertTrue(all(flag is False for flag in cover_flags))
        self.assertEqual(len(cover_flags), len(outputs))

    def test_autotest_requires_results_dir(self) -> None:
        report = PdfReport(self._build_document())
        with self.assertRaises(RuntimeError):
            report._render_autotest_result("TEST-001")

    def test_autotest_raises_for_missing_test_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            report = PdfReport(self._build_document(), test_results_dir=tmpdir)
            with self.assertRaises(FileNotFoundError):
                report._render_autotest_result("TEST-404")

    def test_autotest_renders_markdown_with_staged_images(self) -> None:
        with tempfile.TemporaryDirectory() as results_dir:
            test_id = "TEST-IMG-001"
            test_folder = Path(results_dir) / test_id
            screenshots_folder = test_folder / "screenshots"
            screenshots_folder.mkdir(parents=True, exist_ok=True)
            (screenshots_folder / "screenshot-001.png").write_bytes(b"fake-png")
            (test_folder / "result.md").write_text(
                "\n".join(
                    [
                        f"# {test_id}",
                        "",
                        "- Kind: `test`",
                        "- Status: `passed`",
                        "",
                        "## Screenshots",
                        "",
                        "![screenshot-001.png](screenshots/screenshot-001.png)",
                        "",
                    ]
                )
            )
            report = PdfReport(self._build_document(), test_results_dir=results_dir)
            with tempfile.TemporaryDirectory() as workdir:
                cwd = os.getcwd()
                os.chdir(workdir)
                try:
                    latex = report._render_autotest_result(test_id)
                    self.assertIn("autotest-results/TEST-IMG-001/screenshots/screenshot-001.png", latex)
                    staged_image = Path(workdir) / "autotest-results" / "TEST-IMG-001" / "screenshots" / "screenshot-001.png"  # noqa E501
                    self.assertTrue(staged_image.exists())
                finally:
                    os.chdir(cwd)

    def test_handle_code_plaintext_fallback_omits_language(self) -> None:
        report = PdfReport(self._build_document())
        latex = report.md_to_latex(
            [
                {
                    "type": "block_code",
                    "info": "text",
                    "text": "line one\nline two",
                }
            ]
        )
        self.assertIn(r"\begin{lstlisting}", latex)
        self.assertNotIn("[language=text]", latex)

    def test_handle_code_keeps_valid_language(self) -> None:
        report = PdfReport(self._build_document())
        latex = report.md_to_latex(
            [
                {
                    "type": "block_code",
                    "info": "python",
                    "text": "print('ok')",
                }
            ]
        )
        self.assertIn(r"\begin{lstlisting}[language=python]", latex)

    def test_autotest_status_renders_bold_green_title_case(self) -> None:
        with tempfile.TemporaryDirectory() as results_dir:
            test_id = "TEST-STATUS-001"
            test_folder = Path(results_dir) / test_id
            test_folder.mkdir(parents=True, exist_ok=True)
            (test_folder / "result.md").write_text(
                "\n".join(
                    [
                        f"# {test_id}",
                        "",
                        "- Status: `passed`",
                        "",
                    ]
                )
            )

            report = PdfReport(self._build_document(), test_results_dir=results_dir)
            with tempfile.TemporaryDirectory() as workdir:
                cwd = os.getcwd()
                os.chdir(workdir)
                try:
                    latex = report._render_autotest_result(test_id)
                    self.assertIn(r"Status: \textbf{\textcolor{green!50!black}{Passed}}", latex)
                finally:
                    os.chdir(cwd)

    def _render_autotest(self, test_id: str, body: str) -> tuple[str, str]:
        """Helper: render a synthetic result.md and return (latex, stderr)."""
        with tempfile.TemporaryDirectory() as results_dir:
            test_folder = Path(results_dir) / test_id
            test_folder.mkdir(parents=True, exist_ok=True)
            (test_folder / "result.md").write_text(body)
            report = PdfReport(self._build_document(), test_results_dir=results_dir)
            with tempfile.TemporaryDirectory() as workdir:
                cwd = os.getcwd()
                os.chdir(workdir)
                try:
                    err = io.StringIO()
                    with redirect_stderr(err):
                        latex = report._render_autotest_result(test_id)
                    return latex, err.getvalue()
                finally:
                    os.chdir(cwd)

    def test_extract_autotest_result_separates_description_and_assertions(self) -> None:
        body = "\n".join(
            [
                "# TEST-EXT-001",
                "",
                "- Kind: `test`",
                "- Description: A user can sign in.",
                "",
                "## Assertions",
                "",
                "- (REQ-001) Login button enabled",
                "",
                "## Output",
                "",
                "no output",
                "",
            ]
        )
        result = extract_autotest_result(parse_markdown(body), test_id="TEST-EXT-001")
        self.assertEqual(result.description, "A user can sign in.")
        self.assertEqual(result.missing_fields, [])
        self.assertEqual(len(result.assertions_items), 1)
        self.assertEqual(len(result.metadata_items), 1)

    def test_autotest_renders_description_and_assertions_blocks(self) -> None:
        body = "\n".join(
            [
                "# TEST-DESC-001",
                "",
                "- Kind: `test`",
                "- Status: `passed`",
                "- Description: An admin can disable an active user account.",
                "",
                "## Assertions",
                "",
                "- (REQ-001) Disable button is enabled",
                "- (REQ-001) Status changes to `Disabled`",
                "",
            ]
        )
        latex, _ = self._render_autotest("TEST-DESC-001", body)
        self.assertIn(r"\subsubsection*{Description}", latex)
        self.assertIn("An admin can disable an active user account.", latex)
        self.assertIn(r"\subsubsection*{Assertions}", latex)
        self.assertIn("Disable button is enabled", latex)

    def test_autotest_emits_missing_field_warning_when_description_absent(self) -> None:
        body = "\n".join(
            [
                "# TEST-MISS-001",
                "",
                "- Kind: `test`",
                "",
                "## Assertions",
                "",
                "- (REQ-001) Something",
                "",
            ]
        )
        latex, stderr = self._render_autotest("TEST-MISS-001", body)
        self.assertIn(r"\textcolor{red!75!black}{\textbf{Validation gap", latex)
        self.assertIn("description", latex)
        self.assertNotIn("assertions", latex.split("Validation gap")[1].split("}}")[0])
        self.assertIn("missing required fields: description", stderr)

    def test_autotest_emits_warning_when_assertions_absent(self) -> None:
        body = "\n".join(
            [
                "# TEST-MISS-002",
                "",
                "- Kind: `test`",
                "- Description: Something happens.",
                "",
            ]
        )
        latex, stderr = self._render_autotest("TEST-MISS-002", body)
        self.assertIn("Validation gap", latex)
        self.assertIn("assertions", latex.split("Validation gap")[1])
        self.assertIn("missing required fields: assertions", stderr)

    def test_handle_image_uses_sidecar_caption_over_filename_alt(self) -> None:
        with tempfile.TemporaryDirectory() as results_dir:
            test_id = "TEST-CAP-001"
            test_folder = Path(results_dir) / test_id
            screenshots = test_folder / "screenshots"
            screenshots.mkdir(parents=True, exist_ok=True)
            (screenshots / "screenshot-001.png").write_bytes(b"fake")
            (screenshots / "screenshot-001.json").write_text(
                json.dumps(
                    {
                        "caption": "Login page renders for guest user",
                        "requirementId": "REQ-001",
                        "assertionSource": "await expect(page).toHaveURL(/login/)",
                    }
                )
            )
            (test_folder / "result.md").write_text(
                "\n".join(
                    [
                        f"# {test_id}",
                        "",
                        "- Description: Login page renders.",
                        "",
                        "## Assertions",
                        "",
                        "- (REQ-001) URL is /login",
                        "",
                        "## Screenshots",
                        "",
                        "![screenshot-001.png](screenshots/screenshot-001.png)",
                        "",
                    ]
                )
            )
            report = PdfReport(self._build_document(), test_results_dir=results_dir)
            with tempfile.TemporaryDirectory() as workdir:
                cwd = os.getcwd()
                os.chdir(workdir)
                try:
                    latex = report._render_autotest_result(test_id)
                finally:
                    os.chdir(cwd)
        self.assertIn("Login page renders for guest user", latex)
        # Filename alt text should NOT be used as the caption
        self.assertNotIn(r"\caption{screenshot-001.png}", latex)
        # Hyperlinked requirement id must appear inside the caption
        self.assertIn(r"\hyperref[REQ-001]{\textbf{REQ-001}}", latex)
        # Assertion source must appear in monospace below the figure
        self.assertIn(r"await expect(page).toHaveURL", latex)

    def test_handle_image_falls_back_to_caption_missing_marker(self) -> None:
        report = PdfReport(self._build_document())
        # Image with no alt and no sidecar
        latex = report.md_to_latex(
            [
                {
                    "type": "image",
                    "src": "report/img.png",
                    "alt": "",
                    "title": "",
                }
            ]
        )
        self.assertIn("[Caption missing]", latex)
