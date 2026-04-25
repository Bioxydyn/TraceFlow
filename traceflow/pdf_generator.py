import json
import os
import subprocess  # noqa S404
import sys
import tempfile
from contextlib import contextmanager
import shutil
from pkgutil import get_data
import random
import re
import string
from typing import Generator, NamedTuple, Optional

import latex.jinja2

try:
    import cairosvg
except OSError as exc:
    cairosvg = None  # type: ignore[assignment]
    _CAIROSVG_IMPORT_ERROR = exc
else:
    _CAIROSVG_IMPORT_ERROR = None

from traceflow.parser import (
    Document,
    MarkdownDocument,
    RequirementDocument,
    RiskDocument,
    extract_autotest_result,
    parse_markdown,
)
from traceflow.version import __version__

_latex_jinja2_env = latex.jinja2.make_env()


_LATEX_TEXT_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}


def _escape_latex_verbatim(text: str) -> str:
    """Escape LaTeX special chars for safe inclusion in \\texttt{} or text mode."""
    return "".join(_LATEX_TEXT_ESCAPES.get(ch, ch) for ch in text)


class IndividualReportSpec(NamedTuple):
    suffix: str
    title: str
    flags: dict[str, bool]


def load_resource(package: str, filename: str) -> bytes:
    data = get_data(package, filename)
    assert data is not None
    return data


@contextmanager
def isolated_filesystem(temp_path: Optional[str] = None) -> Generator:
    current_directory = os.getcwd()

    user_specified_path = temp_path is not None

    if not user_specified_path:
        temp_path = tempfile.mkdtemp()
    assert temp_path is not None

    try:
        os.chdir(temp_path)
        yield

    finally:
        os.chdir(current_directory)

        preserve_autotemp = os.getenv("TRACEFLOW_KEEP_TEMPS", "1") != "0"
        if not user_specified_path and not preserve_autotemp:
            shutil.rmtree(temp_path)


class PdfReport():
    _RISK_SCALE: dict[str, int] = {
        "very high": 5,
        "critical": 5,
        "catastrophic": 5,
        "extreme": 5,
        "high": 4,
        "major": 4,
        "serious": 4,
        "frequent": 4,
        "medium": 3,
        "moderate": 3,
        "occasional": 3,
        "possible": 3,
        "low": 1,
        "minor": 1,
        "remote": 1,
        "unlikely": 1,
        "very low": 1,
        "negligible": 1,
        "rare": 1,
        "improbable": 1,
    }

    _RISK_LEVELS: list[tuple[int, str, str]] = [
        (16, "Critical", "red!60"),
        (9, "High", "orange!65"),
        (4, "Medium", "yellow!40"),
        (1, "Low", "green!35"),
    ]

    @staticmethod
    def process_text_impl(text: str, unique_ids: set[str]) -> str:

        # Replace any instances of a unique ID within the text to a link to the ID.
        # 1. Explode text into words
        words = text.split()

        # 2. For each word, check if it is a unique ID. unique_ids is a set, so this is O(1)
        for index, word in enumerate(words):
            leading = len(word) - len(word.lstrip("()[]{}.,;:<>"))
            trailing = len(word.rstrip("()[]{}.,;:<>"))
            prefix = word[:leading]
            suffix = word[trailing:]
            core = word[leading:trailing] if trailing > leading else word[leading:]
            altered_word = core.replace(":", "")
            if altered_word in unique_ids and core:
                words[index] = f"{prefix}\\hyperref[{altered_word}]{{\\textbf{{{core}}}}}{suffix}"

        # 3. Rebuild the text
        new_text = " ".join(words)

        # 4. Did we originally have whitespace at the start or end - if so, add it back
        if text.startswith(" "):
            new_text = " " + new_text
        if text.endswith(" "):
            new_text += " "
        if text.startswith("\n"):
            new_text = "\n" + new_text
        if text.endswith("\n"):
            new_text += "\n"

        # Regular expression to find text wrapped in backticks
        inline_code_pattern = r'`([^`]*)`'

        # Replace the text wrapped in backticks with LaTeX inline code
        new_text = re.sub(inline_code_pattern, r'\\texttt{\1}', new_text)

        return new_text.replace(r"&", r"\&").replace(r"_", r"\_")

    def process_text(self, text: str) -> str:
        return self.process_text_impl(text, self.unique_ids)

    @staticmethod
    def _build_label_from_text(prefix: str, text: str) -> str:
        base = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
        if not base:
            base = "doc"
        return f"{prefix}-{base}"

    @classmethod
    def _score_risk_dimension(cls: type['PdfReport'], value: str) -> int:
        if not value:
            return 0
        digits = re.findall(r"\d+", value)
        if digits:
            try:
                return int(digits[0])
            except ValueError:
                pass
        normalised = re.sub(r"[^a-z ]", "", value.lower()).strip()
        return cls._RISK_SCALE.get(normalised, 0)

    @classmethod
    def _evaluate_risk_rating(cls: type['PdfReport'], severity: str, probability: str) -> tuple[str, int, str]:
        severity_score = cls._score_risk_dimension(severity)
        probability_score = cls._score_risk_dimension(probability)
        score = severity_score * probability_score
        if score == 0:
            return "", 0, ""
        for threshold, label, colour in cls._RISK_LEVELS:
            if score >= threshold:
                return label, score, colour
        return "", score, ""

    def _format_risk_rating_cell(self, label: str, score: int, colour: str) -> str:
        if not label:
            return ""
        cell_text = self.process_text(f"{label} ({score})")
        if colour:
            return f"\\cellcolor{{{colour}}}{cell_text}"
        return cell_text

    @staticmethod
    def _begin_a3_landscape_block(*, clearpage: bool = True) -> list[str]:
        lines: list[str] = []
        if clearpage:
            lines.append("\\clearpage")
        lines.extend(
            [
                "\\begingroup",
                "\\setlength{\\paperwidth}{420mm}",
                "\\setlength{\\paperheight}{297mm}",
                "\\pdfpagewidth=420mm",
                "\\pdfpageheight=297mm",
                "\\special{papersize=420mm,297mm}",
                "\\newgeometry{left=15mm,right=15mm,top=20mm,bottom=20mm}",
                "\\setlength{\\textwidth}{390mm}",
                "\\setlength{\\columnwidth}{\\textwidth}",
                "\\setlength{\\linewidth}{\\textwidth}",
                "\\setlength{\\hsize}{\\textwidth}",
                "\\setlength{\\headwidth}{\\textwidth}",
                "\\fancyhead[L]{\\includegraphics[width=\\traceflowwidelogowidth,keepaspectratio]{\\traceflowtopleftlogo}}",  # noqa E501
                "\\fancyhead[R]{\\includegraphics[width=\\traceflowwidelogowidth,keepaspectratio]{\\traceflowtoprightlogo}}",  # noqa E501
            ]
        )
        return lines

    @staticmethod
    def _end_a3_landscape_block() -> list[str]:
        return [
            "\\restoregeometry",
            "\\setlength{\\headwidth}{\\textwidth}",
            "\\fancyhead[L]{\\includegraphics[width=\\traceflowstandardlogowidth,keepaspectratio]{\\traceflowtopleftlogo}}",  # noqa E501
            "\\fancyhead[R]{\\includegraphics[width=\\traceflowstandardlogowidth,keepaspectratio]{\\traceflowtoprightlogo}}",  # noqa E501
            "\\setlength{\\paperwidth}{210mm}",
            "\\setlength{\\paperheight}{297mm}",
            "\\pdfpagewidth=210mm",
            "\\pdfpageheight=297mm",
            "\\special{papersize=210mm,297mm}",
            "\\endgroup",
            "\\clearpage",
        ]

    def build_traceability_matrix(self, req_page: RequirementDocument, *, link_tests: bool = True) -> str:
        # Display the traceability matrix
        table = "\\subsection{Traceability Matrix}\n\n"

        # Create the table data
        columns: list[str] = []

        for requirement in req_page.items:
            if requirement.test_ids is not None:
                columns += requirement.test_ids
        columns = list(set(columns))
        columns.sort()
        large_matrix = len(columns) > 15

        # Split columns into chunks so that we don't exceed the maximum number of columns
        chunked_columns = []
        max_columns = 30 if self.traceability_a3 else 17
        while len(columns) > 0:
            chunked_columns.append(columns[:max_columns])
            columns = columns[max_columns:]

        for chunk in chunked_columns:
            if self.traceability_a3:
                table += "\n".join(self._begin_a3_landscape_block(clearpage=True)) + "\n"
            elif large_matrix:
                # This is a large matrix. We put the table on its own page, and that page in landscape mode
                table += "\\newpage\n"
                table += "\\begin{landscape}\n"

            table += "\\setlength\\tabcolsep{0pt}\n"
            # Build the table. There should be a tick in the cell if the test is linked to the requirement,
            # otherwise it should be empty
            table += "\\begingroup\n"
            table += "\\rowcolors{2}{gray!25}{white}\n"
            table += "\\begin{table}[H]\n"
            table += "\\centering\n"
            column_width = "1.05cm" if self.traceability_a3 else "1cm"
            table += (
                "\\begin{tabular}{@{}c@{}" + f"@{{}}>{{\\centering\\arraybackslash}}m{{{column_width}}}@{{}}" * len(chunk) + "}\n"  # noqa E501
            )
            table += "\\hline\n"

            # Header row with test ids
            header = "\\diagbox{\\textbf{\\textit{Req ID}}}{\\textbf{\\textit{Test ID}}}"
            for test_id in chunk:
                linked_test = "\\hyperref[" + test_id + "]{" + test_id + "}" if link_tests else test_id
                header += f" & \\rot{{{linked_test}}}"
            header += " \\\\\n\\hline\n"
            table += header

            # For each requirement, generate a row
            for r in req_page.items:
                row = ""
                if not r.test_ids:
                    row += "\\rowcolor{red}"
                row += "\\hyperref[" + r.req_id + "]{" + r.req_id + "}"
                for test_id in chunk:
                    if test_id in r.test_ids:
                        checkmark = "$\\checkmark$"
                        if link_tests:
                            checkmark = "\\hyperref[" + test_id + "]{" + checkmark + "}"
                        row += f" & {checkmark}"
                    else:
                        row += " & "
                row += " \\\\\n\\hline\n"
                table += row

            table += "\\end{tabular}\n"
            table += "\\end{table}\n\n"
            table += "\\endgroup\n"

            if self.traceability_a3:
                table += "\n".join(self._end_a3_landscape_block()) + "\n"
            elif large_matrix:
                table += "\\end{landscape}\n"
                table += "\\newpage\n"

        return table

    def render_markdown_document(self, doc: MarkdownDocument) -> str:
        heading = doc.title
        if doc.category == "design":
            heading = f"Design - {doc.title}"
        elif doc.category not in {"general", "design"}:
            heading = f"{doc.category.title()} - {doc.title}"
        label = self._build_label_from_text(doc.category or "doc", doc.title)
        latex = "\\section{" + self.process_text(heading) + "}\\label{" + label + "}\n\n"
        latex += self.md_to_latex(doc.content)
        latex += "\n\n\\newpage\n\n"
        return latex

    @classmethod
    def _classify_risk_score(cls: type['PdfReport'], score: int) -> tuple[str, str]:
        if score <= 0:
            return "", ""
        for threshold, label, colour in cls._RISK_LEVELS:
            if score >= threshold:
                return label, colour
        return "", ""

    @classmethod
    def _risk_band_ranges(cls: type['PdfReport']) -> list[tuple[str, str, str]]:
        levels = sorted(cls._RISK_LEVELS, key=lambda entry: entry[0])
        ranges: list[tuple[str, str, str]] = []
        for index, (threshold, label, colour) in enumerate(levels):
            if index + 1 < len(levels):
                upper = levels[index + 1][0] - 1
                range_text = f"{threshold}--{upper}" if upper > threshold else f"{threshold}"
            else:
                range_text = f"{threshold}+"
            ranges.append((label, range_text, colour))
        return list(reversed(ranges))

    def build_risk_scoring_matrix(self) -> str:
        axis_labels: list[tuple[str, int]] = [
            ("Low", 1),
            ("Medium", 3),
            ("High", 4),
            ("Very High", 5),
        ]

        lines: list[str] = [
            "\\subsection{Risk Scoring Method}",
            "",
            (
                "Each risk is assessed on two dimensions, \\textbf{Severity} and \\textbf{Probability}. "
                "Each dimension is scored on the scale below, and the overall risk rating is the product "
                "of the two scores (Severity $\\times$ Probability)."
            ),
            "",
            "\\begin{center}",
            "\\begin{tabular}{lc}",
            "\\toprule",
            "\\textbf{Level} & \\textbf{Score} \\\\",
            "\\midrule",
            "Low / Minor / Rare / Unlikely & 1 \\\\",
            "Medium / Moderate / Possible & 3 \\\\",
            "High / Major / Frequent & 4 \\\\",
            "Very High / Critical / Catastrophic & 5 \\\\",
            "\\bottomrule",
            "\\end{tabular}",
            "\\end{center}",
            "",
            "The resulting product determines the overall risk band:",
            "",
            "\\begin{center}",
            "\\begin{tabular}{lcl}",
            "\\toprule",
            "\\textbf{Band} & \\textbf{Score range} & \\textbf{Colour} \\\\",
            "\\midrule",
        ]
        for label, range_text, colour in self._risk_band_ranges():
            lines.append(
                f"\\cellcolor{{{colour}}}{label} & {range_text} & \\cellcolor{{{colour}}}~ \\\\"
            )
        lines.extend([
            "\\bottomrule",
            "\\end{tabular}",
            "\\end{center}",
            "",
            (
                "The matrix below shows every combination of Severity and Probability, the resulting "
                "score, and the band into which that score falls."
            ),
            "",
            "\\begin{center}",
            "\\renewcommand{\\arraystretch}{1.5}",
            "\\setlength{\\tabcolsep}{6pt}",
            "\\begin{tabular}{l|cccc}",
            "\\toprule",
            " & \\multicolumn{4}{c}{\\textbf{Probability}} \\\\",
        ])
        header_cells = " & ".join(f"{name} ({score})" for name, score in axis_labels)
        lines.append(f"\\textbf{{Severity}} & {header_cells} \\\\")
        lines.append("\\midrule")
        for sev_name, sev_score in reversed(axis_labels):
            row_cells: list[str] = [f"{sev_name} ({sev_score})"]
            for prob_name, prob_score in axis_labels:
                product = sev_score * prob_score
                band_label, band_colour = self._classify_risk_score(product)
                if band_colour:
                    cell = f"\\cellcolor{{{band_colour}}}{product} ({band_label})"
                else:
                    cell = f"{product}"
                row_cells.append(cell)
            lines.append(" & ".join(row_cells) + " \\\\")
        lines.extend([
            "\\bottomrule",
            "\\end{tabular}",
            "\\end{center}",
            "",
            "\\subsection{Risk Register}",
            "",
        ])
        return "\n".join(lines) + "\n"

    def build_risk_register(self, risk_page: RiskDocument) -> str:
        if not risk_page.items:
            return ""

        preamble = self.build_risk_scoring_matrix()

        column_fragments = [
            "@{}p{4.0cm}",
            "p{5.0cm}",
            "p{4.2cm}",
            "p{4.2cm}",
            "p{3.4cm}",
            "p{6.3cm}",
            "p{3.6cm}",
            "p{4.7cm}",
            "@{}",
        ]
        column_spec = "".join(column_fragments)
        controls_width = 6.3
        residual_width = 3.6
        test_evidence_width = 4.7

        table_lines = self._begin_a3_landscape_block(clearpage=True)
        table_lines.extend(
            [
                "\\footnotesize",
                "\\setlength\\tabcolsep{3pt}",
                "\\renewcommand{\\arraystretch}{1.3}",
                "\\setlength\\LTleft{0pt}",
                "\\setlength\\LTright{0pt}",
                f"\\begin{{longtable}}{{{column_spec}}}",
            ]
        )

        header_cells = [
            "\\textbf{Risk ID}",
            "\\textbf{Hazardous Situation}",
            "\\textbf{Harm}",
            "\\textbf{Cause}",
            "\\textbf{Risk (Severity $\\times$ Probability)}",
            "\\textbf{Controls}",
            "\\textbf{Residual Risk}",
            "\\textbf{Test Evidence}",
        ]
        header_row = " & ".join(header_cells) + " \\\\ \\midrule"
        table_lines.extend(
            [
                "\\toprule",
                header_row,
                "\\endfirsthead",
                "\\toprule",
                header_row,
                "\\endhead",
                "\\rowcolors{2}{gray!10}{white}",
            ]
        )

        for index, risk in enumerate(risk_page.items):
            severity = risk.attributes.get("severity", "")
            probability = risk.attributes.get("probability", "")
            residual_severity = risk.attributes.get("residual_severity", "")
            residual_probability = risk.attributes.get("residual_probability", "")
            label, score, colour = self._evaluate_risk_rating(severity, probability)
            residual_label, residual_score, residual_colour = self._evaluate_risk_rating(
                residual_severity, residual_probability
            )
            risk_level_cell = self._format_risk_rating_cell(label, score, colour)
            residual_level_cell = self._format_risk_rating_cell(residual_label, residual_score, residual_colour)
            residual_risk_text = risk.attributes.get("residual_risk", "")
            controls_text = self.process_text(risk.attributes.get("controls", ""))

            risk_expression = (
                f"{self.process_text(severity)} $\\times$ {self.process_text(probability)} = {risk_level_cell}"
            )

            control_lines = [controls_text] if controls_text else ["-"]
            controls_content = " \\\\ ".join(control_lines)
            controls_section = f"\\parbox[t]{{{controls_width}cm}}{{{controls_content}}}"

            residual_lines = [
                f"{self.process_text(residual_severity)} $\\times$ {self.process_text(residual_probability)}"
                f" = {residual_level_cell if residual_level_cell else '-'}"
            ]
            if residual_risk_text:
                residual_lines.append(self.process_text(residual_risk_text))
            residual_content = " \\\\ ".join([line for line in residual_lines if line] or ["-"])
            residual_cell = f"\\parbox[t]{{{residual_width}cm}}{{{residual_content}}}"
            test_evidence_text = self.process_text(risk.attributes.get("test_evidence", ""))
            test_evidence_content = test_evidence_text if test_evidence_text else "-"
            test_evidence_cell = f"\\parbox[t]{{{test_evidence_width}cm}}{{{test_evidence_content}}}"
            title_text = self.process_text(risk.title)
            id_text = self.process_text_impl(risk.risk_id, set())
            first_cell = (
                f"\\phantomsection\\label{{{risk.risk_id}}}"
                f"\\hyperref[{risk.risk_id}]{{{id_text}: {title_text}}}"
            )
            row_cells = [
                first_cell,
                self.process_text(risk.attributes.get("hazardous_situation", "")),
                self.process_text(risk.attributes.get("harm", "")),
                self.process_text(risk.attributes.get("cause", "")),
                risk_expression,
                controls_section,
                residual_cell,
                test_evidence_cell,
            ]
            row_ending = " \\\\ \\midrule"
            if index == len(risk_page.items) - 1:
                row_ending = " \\\\"
            table_lines.append(" & ".join(row_cells) + row_ending)

        table_lines.extend(["\\bottomrule", "\\end{longtable}"])
        table_lines.extend(self._end_a3_landscape_block())
        return preamble + "\n".join(table_lines)

    def render_risk_document(self, risk_page: RiskDocument) -> str:
        label = self._build_label_from_text("risk", risk_page.title)
        latex = "\\section{" + self.process_text(risk_page.title) + "}\\label{" + label + "}\n\n"
        latex += self.md_to_latex(risk_page.generic_content)
        latex += "\n\n"
        latex += self.build_risk_register(risk_page)
        return latex

    def md_to_latex(self, items: list[dict]) -> str:

        def handle_paragraph(item: dict) -> str:
            latex = ["\n"]
            for child in item["children"]:
                if child["type"] == "text":
                    latex.append(self.process_text(child["text"]))
                else:
                    latex.append(handle_item(child))
            return "".join(latex)

        def handle_link(item: dict) -> str:
            link_url = item["link"]
            link_text = self.md_to_latex(item["children"])
            return f"\\href{{{link_url}}}{{{link_text}}}"

        def handle_heading(item: dict) -> str:
            level = item["level"]
            return "\\" + "sub" * (level - 1) + "section{" + self.process_text(item["children"][0]["text"]) + "}"

        def handle_list(item: dict) -> str:
            latex = ["\\begin{itemize}"]
            for list_item in item["children"]:
                latex.append("\n\\item ")
                for child in list_item["children"]:
                    if child["type"] == "text":
                        latex.append(self.process_text(child["text"]))
                    if child["type"] == "block_text":
                        latex.append(render_children(child.get("children", [])))

            latex.append("\n\\end{itemize}")
            return "".join(latex)

        def handle_inline_html(item: dict) -> str:
            text = item.get("text", "").strip().lower()
            if text in {"<br>", "<br/>", "<br />"}:
                return "\\\\ "
            return ""

        def handle_image(item: dict) -> str:
            url = item["src"]
            sidecar = item.get("_sidecar") or {}
            alt_text_raw = item.get("alt", "")
            caption_raw = sidecar.get("caption") or ""
            if not caption_raw.strip():
                # Fall back to alt text, but skip filename-shaped alts (e.g. "screenshot-001.png")
                # which look like a caption to LaTeX but tell the reader nothing.
                if alt_text_raw and not re.fullmatch(r"[A-Za-z0-9._-]+\.(png|jpg|jpeg|gif|svg|pdf)", alt_text_raw.strip(), re.IGNORECASE):
                    caption_raw = alt_text_raw

            assertion_source = (sidecar.get("assertionSource") or "").strip()
            requirement_id = (sidecar.get("requirementId") or "").strip()
            caption_parts: list[str] = []
            if caption_raw.strip():
                caption_parts.append(self.process_text(caption_raw.strip()))
            if requirement_id:
                caption_parts.append(
                    f"\\textit{{(evidence for {self.process_text(requirement_id)})}}"
                )
            if not caption_parts:
                caption_parts.append(r"\textcolor{red!75!black}{\textbf{[Caption missing]}}")

            latex = [
                '\n\\begin{figure}[H]',
                '\n\\centering',
                f'\n\\includegraphics[width=0.7\\textwidth]{{{url}}}',
                "\n\\caption{" + " ".join(caption_parts) + "}",
            ]
            if assertion_source:
                truncated = assertion_source if len(assertion_source) <= 200 else assertion_source[:197] + "..."
                latex.append(
                    "\n\\par\\noindent\\small\\texttt{"
                    + _escape_latex_verbatim(truncated)
                    + "}\\normalsize"
                )
            latex.append('\n\\end{figure}')
            return ''.join(latex)

        def handle_block_code(item: dict) -> str:
            code_type = None
            if "info" in item:
                raw_info = str(item["info"]).strip()
                code_type = raw_info.split()[0] if raw_info else None

            if code_type == "mermaid":
                return handle_mermaid(item)
            if code_type == "raw":
                return item["text"]
            if code_type == "manualtest":
                return handle_manual_test(item)
            if code_type == "testcoverpage":
                return handle_test_cover_page(item)
            if code_type == "autotest":
                return handle_auto_test(item)
            return handle_code(item)

        def render_children(children: list[dict]) -> str:
            fragments: list[str] = []
            for child in children:
                fragments.append(handle_item(child))
            return "".join(fragments)

        def handle_table(item: dict) -> str:
            header_cells: list[str] = []
            body_rows: list[list[str]] = []
            for head_cell in item.get("children", [])[0]["children"]:
                header_cells.append(render_children(head_cell.get("children", [])))
            body_section = item.get("children", [])[1]
            for row in body_section["children"]:
                row_values = [render_children(cell.get("children", [])) for cell in row["children"]]
                body_rows.append(row_values)

            column_spec = "|".join(["X"] * max(len(header_cells), 1))
            latex_lines = [
                "\\begingroup",
                "\\begin{table}[h]",
                "\\centering",
                "\\rowcolors{2}{gray!10}{white}",
                f"\\begin{{tabularx}}{{\\linewidth}}{{|{column_spec}|}}",
                "\\hline",
            ]
            latex_lines.append(" & ".join(f"\\textbf{{{cell}}}" for cell in header_cells) + " \\\\ \\hline")
            for row in body_rows:
                latex_lines.append(" & ".join(row) + " \\\\ \\hline")
            latex_lines.append("\\end{tabularx}")
            latex_lines.append("\\end{table}")
            latex_lines.append("\\endgroup")
            return "\n".join(latex_lines)

        def handle_test_cover_page(_: dict) -> str:
            return r"""
\begin{table}[h]
\renewcommand{\arraystretch}{1.35}
\arrayrulecolor{gray} % Set the color of the horizontal and vertical lines to gray
\begin{tabular}{|>{\columncolor{gray!30}}m{0.45\linewidth}|m{0.45\linewidth}|}
\hline
\textbf{Tester} & \\
\hline
\textbf{Test Date} & \\
\hline
\textbf{Result} & \\
\hline
\textbf{Observations} \vspace*{1.5\baselineskip} & \\
\hline
\end{tabular}
\end{table}

\vspace{0.3cm}
\newpage

"""

        def handle_manual_test(_: dict) -> str:
            pass_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))  # noqa S311
            fail_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))  # noqa S311
            skip_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))  # noqa S311
            comment_id = ''.join(random.choices(string.ascii_uppercase + string.digits, k=10))  # noqa S311
            return r"""
\noindent
\begin{Form}
\textbf{Pass} \CheckBox[name=""" + pass_id + r"""]{} \hspace{2cm} \textbf{Fail} \CheckBox[name=""" + fail_id + r"""]{} \hspace{2cm} \textbf{Skip} \CheckBox[name=""" + skip_id + r"""]{} \\
\vspace{0.05cm}
\textbf{Comments} \\
\TextField[name=""" + comment_id + r""", multiline=true, width=\linewidth, height=1.2cm]{}
\end{Form}
        """  # noqa E501

        def handle_auto_test(item: dict) -> str:
            content = item.get("text", "")
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            if not lines:
                raise ValueError("autotest blocks must include a test ID.")
            if len(lines) > 1:
                raise ValueError("autotest blocks support exactly one test ID.")
            return self._render_autotest_result(lines[0])

        def handle_code(item: dict) -> str:
            language: Optional[str] = None
            if "info" in item:
                raw_info = str(item["info"]).strip()
                language_token = raw_info.split()[0] if raw_info else ""
                if language_token and re.fullmatch(r"[A-Za-z0-9_+#-]+", language_token):
                    if language_token.lower() not in {"text", "plaintext", "txt", "plain"}:
                        language = language_token
            code_content = item["text"]

            if language:
                return f"\\begin{{lstlisting}}[language={language}]\n{code_content}\n\\end{{lstlisting}}"
            return f"\\begin{{lstlisting}}\n{code_content}\n\\end{{lstlisting}}"

        def handle_mermaid(item: dict) -> str:
            svg_path = PdfReport.get_temporary_filename(suffix=".svg", force_random=True)
            mmd_path = PdfReport.get_temporary_filename(suffix=".mmd", force_random=True)

            mermaid_code = item["text"]
            caption_text = ""
            mermaid_lines = mermaid_code.splitlines()
            if mermaid_lines:
                first_line = mermaid_lines[0].strip()
                caption_match = re.match(r"^%%\s*caption\s*:\s*(.+)$", first_line, re.IGNORECASE)
                if caption_match:
                    caption_text = caption_match.group(1).strip()
                    mermaid_code = "\n".join(mermaid_lines[1:])

            with open(mmd_path, "w") as mmd_file:
                mmd_file.write(mermaid_code)

            subprocess.run(["mmdc", "-i", mmd_path, "-o", svg_path], check=True)  # noqa S607, S603

            pdf_path = svg_path.replace(".svg", ".pdf")

            pdf_rendered = False
            try:
                # Direct PDF export via mermaid-cli keeps text/layout fidelity.
                subprocess.run(["mmdc", "-i", mmd_path, "-o", pdf_path, "-f"], check=True)  # noqa S607, S603
                pdf_rendered = True
            except subprocess.CalledProcessError:
                pdf_rendered = False

            if not pdf_rendered:
                if cairosvg is None:
                    raise RuntimeError(
                        "Rendering mermaid diagrams requires cairosvg, but importing it failed"
                        f" with: {_CAIROSVG_IMPORT_ERROR}"
                    )
                cairosvg.svg2pdf(url=svg_path, write_to=pdf_path)
            return handle_image({"src": pdf_path, "alt": caption_text, "title": "", "type": "image"})

        def handle_item(item: dict) -> str:
            handlers = {
                "text": lambda item: self.process_text(item["text"]),
                "paragraph": handle_paragraph,
                "link": handle_link,
                "heading": handle_heading,
                "list": handle_list,
                "image": handle_image,
                "table": handle_table,
                "block_code": handle_block_code,
                "blank_line": lambda _: "\n",
                "newline": lambda _: "\n",
                "inline_html": handle_inline_html,
                "strong": lambda item: f"\\textbf{{{self.process_text(item['children'][0]['text'])}}}",
                "emphasis": lambda item: f"\\emph{{{self.process_text(item['children'][0]['text'])}}}",
                "softbreak": lambda _: "\n",
                "codespan": lambda item: f"\\texttt{{{self.process_text(item['text'])}}}",
                "raw_latex": lambda item: item.get("text", ""),
                "linebreak": lambda _: "\n",
            }
            handler = handlers.get(item["type"])
            if handler:
                return handler(item)
            print(f"Unknown item type: {item['type']}")
            print(item)
            return ""

        latex = []
        for item in items:
            latex.append(handle_item(item))
        return "\n".join(latex)

    @staticmethod
    def _extract_ast_heading_text(item: dict) -> str:
        if item.get("type") != "heading":
            return ""
        text_fragments: list[str] = []
        for child in item.get("children", []):
            if child.get("type") in {"text", "codespan"}:
                text_fragments.append(child.get("text", ""))
        return "".join(text_fragments).strip()

    def _render_test_steps_expected_columns(self, steps_items: list[dict], expected_items: list[dict]) -> str:
        steps_latex = self.md_to_latex(steps_items).strip() or r"\textit{Not provided.}"
        expected_latex = self.md_to_latex(expected_items).strip() or r"\textit{Not provided.}"
        return (
            r"\begingroup" "\n"
            r"\rowcolors{2}{white}{white}" "\n"
            r"\begin{table}[H]" "\n"
            r"\setlength{\tabcolsep}{4pt}" "\n"
            r"\renewcommand{\arraystretch}{1.05}" "\n"
            r"\begin{tabularx}{\linewidth}{|>{\raggedright\arraybackslash}X|>{\raggedright\arraybackslash}X|}" "\n"
            r"\hline" "\n"
            r"\rowcolor{gray!15}\textbf{Test Steps} & \textbf{Expected Result} \\" "\n"
            r"\hline" "\n"
            r"\begin{minipage}[t]{\linewidth}\vspace{0pt}" "\n"
            + steps_latex + "\n"
            + r"\end{minipage} &" "\n"
            + r"\begin{minipage}[t]{\linewidth}\vspace{0pt}" "\n"
            + expected_latex + "\n"
            + r"\end{minipage} \\" "\n"
            + r"\hline" "\n"
            + r"\end{tabularx}" "\n"
            + r"\end{table}" "\n"
            + r"\endgroup"
        )

    def render_test_content(self, items: list[dict]) -> str:
        steps_heading_index: Optional[int] = None
        expected_heading_index: Optional[int] = None

        for index, item in enumerate(items):
            if item.get("type") != "heading" or item.get("level") != 3:
                continue
            heading_text = self._extract_ast_heading_text(item).lower().rstrip(":")
            if steps_heading_index is None and heading_text in {"test steps", "steps"}:
                steps_heading_index = index
                continue
            if (
                steps_heading_index is not None
                and heading_text in {"expected result", "expected outcome", "expected output"}
            ):
                expected_heading_index = index
                break

        if (
            steps_heading_index is None
            or expected_heading_index is None
            or expected_heading_index <= steps_heading_index
        ):
            return self.md_to_latex(items)

        expected_end = len(items)
        for index in range(expected_heading_index + 1, len(items)):
            item = items[index]
            if item.get("type") == "heading" and item.get("level", 99) <= 3:
                expected_end = index
                break

        prefix = items[:steps_heading_index]
        steps_items = items[steps_heading_index + 1:expected_heading_index]
        expected_items = items[expected_heading_index + 1:expected_end]
        suffix = items[expected_end:]

        fragments: list[str] = []
        if prefix:
            fragments.append(self.md_to_latex(prefix))
        fragments.append(self._render_test_steps_expected_columns(steps_items, expected_items))
        if suffix:
            fragments.append(self.md_to_latex(suffix))
        return "\n".join(fragment for fragment in fragments if fragment)

    @staticmethod
    def _sanitize_path_component(value: str) -> str:
        sanitized = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
        return sanitized or "autotest"

    @staticmethod
    def _is_external_reference(path: str) -> bool:
        lower = path.lower()
        return lower.startswith("http://") or lower.startswith("https://") or lower.startswith("/")

    @staticmethod
    def _iter_ast_nodes(item: dict) -> Generator[dict, None, None]:
        yield item
        for child in item.get("children", []):
            yield from PdfReport._iter_ast_nodes(child)

    @staticmethod
    def _prefix_image_sources(items: list[dict], prefix: str) -> list[dict]:
        for item in items:
            for node in PdfReport._iter_ast_nodes(item):
                if node.get("type") != "image":
                    continue
                src = node.get("src", "")
                if not src or PdfReport._is_external_reference(src):
                    continue
                normalised = src.replace("\\", "/").lstrip("./")
                node["src"] = f"{prefix}/{normalised}"
        return items

    @staticmethod
    def _attach_image_sidecar_metadata(items: list[dict], staged_dir: str) -> list[dict]:
        """Decorate every staged image node with a ``_sidecar`` dict if a JSON sidecar exists.

        For ``staged_dir/screenshots/screenshot-001.png`` the loader looks for
        ``staged_dir/screenshots/screenshot-001.json``. Sidecar fields are merged untouched.
        """
        for item in items:
            for node in PdfReport._iter_ast_nodes(item):
                if node.get("type") != "image":
                    continue
                src = node.get("src", "")
                if not src or PdfReport._is_external_reference(src):
                    continue
                # ``src`` here is already prefixed with ``autotest-results/<staged_name>/...``
                # (see ``_prefix_image_sources``). The sidecar lives next to the image on
                # disk in the original ``staged_dir``.
                relative_inside_staged = src.split(staged_dir.replace("\\", "/").rstrip("/") + "/", 1)[-1]
                sidecar_path = os.path.join(staged_dir, os.path.splitext(relative_inside_staged)[0] + ".json")
                if not os.path.isfile(sidecar_path):
                    continue
                try:
                    with open(sidecar_path, "r", encoding="utf-8") as handle:
                        node["_sidecar"] = json.load(handle)
                except (OSError, json.JSONDecodeError):
                    continue
        return items

    @staticmethod
    def _normalise_autotest_headings(items: list[dict]) -> list[dict]:
        for item in items:
            if item.get("type") == "heading":
                level = int(item.get("level", 1))
                item["level"] = min(3, max(1, level + 2))
        return items

    @staticmethod
    def _normalise_status_label(value: str) -> str:
        cleaned = re.sub(r"[_-]+", " ", value.strip())
        if not cleaned:
            return ""
        return " ".join(part.capitalize() for part in cleaned.split())

    @staticmethod
    def _status_colour(value: str) -> str:
        status = value.strip().lower()
        if status in {"passed", "pass", "success", "succeeded"}:
            return "green!50!black"
        if status in {"failed", "fail", "error"}:
            return "red!70!black"
        if status in {"skipped", "skip"}:
            return "gray!70!black"
        if status in {"running", "in progress"}:
            return "blue!65!black"
        if status in {"cancelled", "canceled", "warning"}:
            return "orange!80!black"
        return "black"

    @classmethod
    def _style_autotest_statuses(cls: type['PdfReport'], items: list[dict]) -> list[dict]:
        for item in items:
            for node in cls._iter_ast_nodes(item):
                if node.get("type") != "list_item":
                    continue
                list_children = node.get("children", [])
                if len(list_children) != 1 or list_children[0].get("type") != "block_text":
                    continue
                inline_children = list_children[0].get("children", [])
                if len(inline_children) < 2:
                    continue

                label_node = inline_children[0]
                value_node = inline_children[1]
                if label_node.get("type") != "text" or value_node.get("type") != "codespan":
                    continue
                if label_node.get("text", "").strip().lower() != "status:":
                    continue

                raw_status = str(value_node.get("text", "")).strip()
                status_label = cls._normalise_status_label(raw_status)
                if not status_label:
                    continue
                colour = cls._status_colour(raw_status)

                inline_children[0]["text"] = "Status: "
                inline_children[1] = {
                    "type": "raw_latex",
                    "text": f"\\textbf{{\\textcolor{{{colour}}}{{{status_label}}}}}",
                }
        return items

    def _resolve_autotest_result_file(self, test_id: str) -> tuple[str, str]:
        if not self.test_results_dir:
            raise RuntimeError(
                "autotest blocks require --test-results-dir to be set."
            )
        source_dir = os.path.join(self.test_results_dir, test_id)
        if not os.path.isdir(source_dir):
            raise FileNotFoundError(
                f"Autotest ID `{test_id}` not found in test results directory: {self.test_results_dir}"
            )
        result_file = os.path.join(source_dir, "result.md")
        if not os.path.isfile(result_file):
            raise FileNotFoundError(
                f"Autotest result markdown missing for `{test_id}`: {result_file}"
            )
        return source_dir, result_file

    def _render_autotest_result(self, test_id: str) -> str:
        source_dir, result_file = self._resolve_autotest_result_file(test_id)
        staged_root = "autotest-results"
        os.makedirs(staged_root, exist_ok=True)
        staged_name = self._sanitize_path_component(test_id)
        staged_dir = os.path.join(staged_root, staged_name)
        if os.path.isdir(staged_dir):
            shutil.rmtree(staged_dir)
        shutil.copytree(source_dir, staged_dir)

        with open(result_file, "r", encoding="utf-8") as result_handle:
            result_markdown = result_handle.read()

        parsed_result = parse_markdown(result_markdown)
        autotest = extract_autotest_result(parsed_result, test_id=test_id)

        # Build the visible "Description" and "Assertions" blocks ahead of the raw bullets so a
        # reviewer reads intent before metadata. ``autotest.other_items`` keeps any sections
        # other than ``## Assertions`` (e.g. ``## Output``, ``## Screenshots``) in their original
        # order.
        ordered_items: list[dict] = []
        if autotest.metadata_items:
            ordered_items.extend(self._style_autotest_statuses(autotest.metadata_items))
        ordered_items.extend(self._normalise_autotest_headings(autotest.other_items))
        ordered_items = self._prefix_image_sources(ordered_items, f"{staged_root}/{staged_name}")
        ordered_items = self._attach_image_sidecar_metadata(ordered_items, staged_dir)

        latex_fragments: list[str] = []
        if autotest.missing_fields:
            joined = ", ".join(autotest.missing_fields)
            sys.stderr.write(
                f"WARNING: autotest `{test_id}` is missing required fields: {joined}\n"
            )
            latex_fragments.append(
                "\n\\par\\noindent\\textcolor{red!75!black}{\\textbf{"
                + f"Validation gap: this test is missing {joined}. "
                + "Update the test source so reviewers see what was asserted."
                + "}}\n"
            )

        if autotest.description:
            latex_fragments.append(
                "\n\\subsubsection*{Description}\n"
                + self.process_text(autotest.description)
                + "\n"
            )

        if autotest.assertions_items:
            normalised_assertions = self._normalise_autotest_headings(
                [dict(item) for item in autotest.assertions_items]
            )
            latex_fragments.append("\n\\subsubsection*{Assertions}\n")
            latex_fragments.append(self.md_to_latex(normalised_assertions))

        latex_fragments.append(self.md_to_latex(ordered_items))
        return "\n".join(fragment for fragment in latex_fragments if fragment)

    @staticmethod
    def get_global_tex_vars() -> dict[str, str]:
        return {"version": __version__}

    @staticmethod
    def get_temporary_filename(suffix: str = ".temp", force_random: bool = False) -> str:
        if os.getenv("TRACEFLOW_TESTING") and not force_random:
            return "test" + suffix
        return "".join(random.choices(string.ascii_uppercase, k=10)) + suffix # noqa S311

    def _collect_unique_ids(
        self,
        *,
        include_risks: bool = True,
        include_requirements: bool = True,
        include_tests: bool = True,
    ) -> set[str]:
        unique_ids: set[str] = set()
        if include_tests:
            for test_page in self.document.tests:
                for test in test_page.items:
                    unique_ids.add(test.test_id)
        if include_requirements:
            for req_page in self.document.requirements:
                for requirement in req_page.items:
                    unique_ids.add(requirement.req_id)
        if include_risks:
            for risk_page in self.document.risks:
                for risk in risk_page.items:
                    unique_ids.add(risk.risk_id)
        return unique_ids

    def __init__(
        self,
        document: Document,
        *,
        test_results_dir: Optional[str] = None,
        top_left_logo_path: Optional[str] = None,
        top_right_logo_path: Optional[str] = None,
    ):
        self.document = document
        self.top_left_logo_path = top_left_logo_path
        self.top_right_logo_path = top_right_logo_path
        self.test_results_dir: Optional[str] = None
        if test_results_dir:
            resolved_test_results_dir = os.path.abspath(test_results_dir)
            if not os.path.isdir(resolved_test_results_dir):
                raise FileNotFoundError(f"Test results directory does not exist: {resolved_test_results_dir}")
            self.test_results_dir = resolved_test_results_dir
        self.unique_ids = self._collect_unique_ids()
        self.traceability_a3 = False

    @staticmethod
    def _logo_basename(path: Optional[str], default_name: str) -> str:
        if path:
            return os.path.basename(path)
        return default_name

    def _ensure_logo(
        self,
        *,
        filename: str,
        resource_name: str,
        provided_path: Optional[str],
    ) -> None:
        destination = os.path.join(os.getcwd(), filename)
        if provided_path:
            if not os.path.isfile(provided_path):
                raise FileNotFoundError(f"Logo path does not exist: {provided_path}")
            shutil.copy(provided_path, destination)
        else:
            with open(destination, "wb") as f:
                f.write(load_resource("traceflow.res", resource_name))

    def render(
        self,
        *,
        include_design: bool = True,
        include_supplementary: bool = True,
        include_risks: bool = True,
        include_requirements: bool = True,
        include_tests: bool = True,
        report_title: Optional[str] = None,
        include_cover_page: bool = True,
        include_toc: bool = True,
        cover_signature_boxes: bool = False,
        document_code: Optional[str] = None,
        document_type: Optional[str] = None,
        traceability_a3: bool = False,
    ) -> bytes:
        original_unique_ids = self.unique_ids
        original_traceability_a3 = self.traceability_a3
        selected_unique_ids = self._collect_unique_ids(
            include_risks=include_risks,
            include_requirements=include_requirements,
            include_tests=include_tests,
        )
        self.unique_ids = selected_unique_ids
        self.traceability_a3 = traceability_a3
        try:
            header = _latex_jinja2_env.from_string(
                load_resource("traceflow.res", "report-header.tex").decode("utf-8")
            )

            tex_vars = self.get_global_tex_vars()
            default_title = self.document.name + " " + self.document.version + ": Validation Pack"
            tex_vars["report_title"] = self.process_text(report_title or default_title)
            tex_vars["include_cover_page"] = include_cover_page
            tex_vars["include_toc"] = include_toc
            tex_vars["cover_signature_boxes"] = cover_signature_boxes
            tex_vars["document_code"] = self.process_text(document_code or "N/A")
            tex_vars["document_type"] = self.process_text(document_type or "TraceFlow Report")
            top_left_logo_name = PdfReport._logo_basename(self.top_left_logo_path, "traceflow-logo.png")
            top_right_logo_name = PdfReport._logo_basename(self.top_right_logo_path, "voxelflow-logo.png")

            tex_vars["top_left_logo"] = top_left_logo_name
            tex_vars["top_right_logo"] = top_right_logo_name
            document = header.render(**tex_vars)

            # Create the "report" directory if it doesn't exist
            if not os.path.exists("report"):
                os.mkdir("report")

            with isolated_filesystem("report"):

                if include_design:
                    for design_doc in self.document.design_documents:
                        document += self.render_markdown_document(design_doc)

                if include_supplementary:
                    for supplementary_doc in self.document.supplementary_documents:
                        document += self.render_markdown_document(supplementary_doc)

                if include_risks:
                    for risk_page in self.document.risks:
                        document += self.render_risk_document(risk_page)

                if include_requirements:
                    for req_page in self.document.requirements:
                        section_label = self._build_label_from_text("requirements", req_page.title)
                        document += "\\section{" + self.process_text(req_page.title) + "}\\label{" + section_label + "}\n\n"
                        document += self.md_to_latex(req_page.generic_content)

                        document += self.build_traceability_matrix(req_page, link_tests=include_tests)

                        separator = "\\vspace{0.15cm}\n\\noindent\\rule{\\linewidth}{0.4pt}\n\\vspace{0.15cm}\n\n"
                        for requirement_index, requirement in enumerate(req_page.items):
                            document += "\\subsection{" + self.process_text(requirement.req_id + ": " + requirement.title) + "}"
                            document += "\\label{" + requirement.req_id + "}\n\n"

                            linked_test_content: str = ""
                            for test_id_linked in requirement.test_ids:
                                linked_test_content += "**Test ID:** " + test_id_linked + "\n"
                            if linked_test_content != "":
                                document += self.md_to_latex(parse_markdown(linked_test_content))
                                document += "\n\n"

                            document += self.md_to_latex(requirement.content)
                            document += "\n\n"
                            if requirement_index < len(req_page.items) - 1:
                                document += separator

                        document += "\\newpage\n\n"

                if include_tests:
                    for test_page in self.document.tests:
                        section_label = self._build_label_from_text("tests", test_page.title)
                        document += "\\section{" + self.process_text(test_page.title) + "}\\label{" + section_label + "}\n\n"
                        document += self.md_to_latex(test_page.generic_content)

                        separator = "\\vspace{0.15cm}\n\\noindent\\rule{\\linewidth}{0.4pt}\n\\vspace{0.15cm}\n\n"
                        for test_index, test in enumerate(test_page.items):
                            document += "\\subsection{" + self.process_text(test.test_id + ": " + test.title) + "}"
                            document += "\\label{" + test.test_id + "}\n\n"
                            document += self.render_test_content(test.content)
                            document += "\n\n"
                            if test_index < len(test_page.items) - 1:
                                document += separator

                        document += "\\newpage\n\n"

                document += r"%%%%%%%%%%% END DOCUMENT" + "\n\n" r"\label{LastPage}" + "\n\n" + r"\end{document}" + "\n"

                self._ensure_logo(
                    filename=top_left_logo_name,
                    resource_name="traceflow-logo.png",
                    provided_path=self.top_left_logo_path,
                )
                self._ensure_logo(
                    filename=top_right_logo_name,
                    resource_name="voxelflow-logo.png",
                    provided_path=self.top_right_logo_path,
                )

                # Recursively copy all files from the document.input_dir folder to the current folder
                for root, _, files in os.walk(self.document.input_dir):
                    for filename in files:
                        shutil.copy(os.path.join(root, filename), filename)

                output_filename = self.get_temporary_filename(suffix=".tex")

                with open(output_filename, "w") as output_file:
                    output_file.write(document)

                pdflatex_command = f"pdflatex -interaction=nonstopmode -halt-on-error {output_filename}"
                try:
                    subprocess.check_output(
                        pdflatex_command,
                        shell=True,  # noqa: S602
                        stderr=subprocess.STDOUT,
                        universal_newlines=True,
                        errors="replace",
                    )  # nopep8
                    subprocess.check_output(
                        pdflatex_command,
                        shell=True,  # noqa: S602
                        stderr=subprocess.STDOUT,
                        universal_newlines=True,
                        errors="replace",
                    )  # nopep8
                    subprocess.check_output(
                        pdflatex_command,
                        shell=True,  # noqa: S602
                        stderr=subprocess.STDOUT,
                        universal_newlines=True,
                        errors="replace",
                    )  # nopep8
                except subprocess.CalledProcessError as exc:
                    print("Status : FAIL", exc.returncode, exc.output)
                    raise exc

                output_pdf = os.path.splitext(output_filename)[0] + ".pdf"

                with open(output_pdf, "rb") as out:
                    return out.read()
        finally:
            self.unique_ids = original_unique_ids
            self.traceability_a3 = original_traceability_a3

    def _individual_report_specs(self) -> list[IndividualReportSpec]:
        specs: list[IndividualReportSpec] = []
        if self.document.design_documents:
            specs.append(
                IndividualReportSpec(
                    suffix="design",
                    title="Design Documentation",
                    flags={
                        "include_design": True,
                        "include_supplementary": False,
                        "include_risks": False,
                        "include_requirements": False,
                        "include_tests": False,
                    },
                )
            )
        if self.document.supplementary_documents:
            specs.append(
                IndividualReportSpec(
                    suffix="docs",
                    title="Supporting Documentation",
                    flags={
                        "include_design": False,
                        "include_supplementary": True,
                        "include_risks": False,
                        "include_requirements": False,
                        "include_tests": False,
                    },
                )
            )
        if any(risk_doc.items for risk_doc in self.document.risks):
            specs.append(
                IndividualReportSpec(
                    suffix="risks",
                    title="Risk Register",
                    flags={
                        "include_design": False,
                        "include_supplementary": False,
                        "include_risks": True,
                        "include_requirements": False,
                        "include_tests": False,
                    },
                )
            )
        if any(req_doc.items for req_doc in self.document.requirements):
            specs.append(
                IndividualReportSpec(
                    suffix="requirements",
                    title="Requirements",
                    flags={
                        "include_design": False,
                        "include_supplementary": False,
                        "include_risks": False,
                        "include_requirements": True,
                        "include_tests": False,
                    },
                )
            )
        if any(test_doc.items for test_doc in self.document.tests):
            specs.append(
                IndividualReportSpec(
                    suffix="tests",
                    title="Test Plan",
                    flags={
                        "include_design": False,
                        "include_supplementary": False,
                        "include_risks": False,
                        "include_requirements": False,
                        "include_tests": True,
                    },
                )
            )
        return specs

    def render_individual_pdfs(self, base_output_path: str, *, extension: str = ".pdf") -> list[str]:
        output_ext = extension or ".pdf"
        output_files: list[str] = []
        for spec in self._individual_report_specs():
            output_path = f"{base_output_path}-{spec.suffix}{output_ext}"
            title = f"{self.document.name} {self.document.version}: {spec.title}"
            pdf_bytes = self.render(report_title=title, include_cover_page=False, **spec.flags)
            with open(output_path, "wb") as f:
                f.write(pdf_bytes)
            output_files.append(output_path)
        return output_files
