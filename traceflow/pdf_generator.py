import hashlib
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
from typing import TYPE_CHECKING, Generator, NamedTuple, Optional

import latex.jinja2

try:
    import cairosvg
except OSError as exc:
    cairosvg = None  # type: ignore[assignment]
    _CAIROSVG_IMPORT_ERROR = exc
else:
    _CAIROSVG_IMPORT_ERROR = None

from traceflow.evidence import EvidenceRecord, load_autotest_result, load_evidence_record
from traceflow.evidence_render import (
    FIGURE_LABEL_PREFIX,
    EvidenceIndexError,
    LABEL_PREFIX,
    LATEX_TEXT_ESCAPES,
    breakable,
    breakable_code,
    coloured_status,
    evidence_anchor,
    latex_escape,
    listing_ascii,
    parse_aux_pages,
    status_label,
    undefined_references,
    write_evidence_index,
)
from traceflow.parser import (
    Document,
    EvidenceBinding,
    HierarchyConfig,
    MarkdownDocument,
    RequirementDocument,
    RiskDocument,
    Test,
)
from traceflow.report_latex import StructureRenderer
from traceflow.report_structure import (
    DEFAULT_DATE_FORMAT,
    MATRIX_STYLES,
    STRUCTURES,
    DocumentSet,
    DocumentSetError,
    check_commit,
    document_set_errors,
    generated_date,
    plan_hierarchy,
    source_date_epoch,
)
from traceflow.version import __version__

if TYPE_CHECKING:  # pragma: no cover - typing only
    from traceflow.volumes import VolumePlan, VolumeSpec

_latex_jinja2_env = latex.jinja2.make_env()

EVIDENCE_LAYOUTS = ("legacy", "record")
EVIDENCE_MODES = ("inline", "reference")

_LATEX_TEXT_ESCAPES = LATEX_TEXT_ESCAPES
_CODE_SPAN_PLACEHOLDER = re.compile("\x00(\\d+)\x00")


def _escape_latex_verbatim(text: str) -> str:
    """Escape LaTeX special chars for safe inclusion in \\texttt{} or text mode."""
    return latex_escape(text)


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

    _FILENAME_ALT = re.compile(r"[A-Za-z0-9._-]+\.(png|jpg|jpeg|gif|svg|pdf)", re.IGNORECASE)
    # Width as before, plus a height cap so a tall capture never runs off the page.
    _FIGURE_SIZE = r"width=0.7\textwidth,height=0.6\textheight,keepaspectratio"
    # Record layout: the capture at its own size, shrunk to fit the text block if larger.
    _RECORD_FIGURE_BOX = r"max width=\linewidth,max height=0.6\textheight"

    @staticmethod
    def _sidecar_requirement_ids(sidecar: dict) -> list[str]:
        """``requirementIds`` (a list), else the single ``requirementId``, from an image sidecar."""
        ids = sidecar.get("requirementIds")
        if isinstance(ids, list):
            found = [str(item).strip() for item in ids if str(item).strip()]
            if found:
                return found
        single = sidecar.get("requirementId")
        if isinstance(single, str) and single.strip():
            return [single.strip()]
        return []

    # Common Unicode characters that appear in human-written prose but are not in the
    # default LaTeX font encoding (T1) — translate to safe ASCII equivalents so the
    # PDF compile does not fail on input authored in a UTF-8 editor. Anything not on
    # this list still flows through to LaTeX unchanged.
    _UNICODE_LATEX_REPLACEMENTS: dict[str, str] = {
        "→": "->",     # → right arrow
        "←": "<-",     # ← left arrow
        "⇒": "=>",     # ⇒ rightwards double arrow
        "⇐": "<=",     # ⇐ leftwards double arrow
        "↔": "<->",    # ↔ left-right arrow
        "≤": r"\(\leq\)",   # ≤
        "≥": r"\(\geq\)",   # ≥
        "≠": r"\(\neq\)",   # ≠
        "≈": r"\(\approx\)",  # ≈
        "±": r"\(\pm\)",    # ±
        "×": r"\(\times\)",  # ×
        "÷": r"\(\div\)",   # ÷
        "…": r"\dots ",     # …
        "•": r"\textbullet ",  # •
        "–": "--",     # – en dash
        "—": "---",    # — em dash
        "‘": "`",      # ‘
        "’": "'",      # ’
        "“": "``",     # “
        "”": "''",     # ”
    }

    @classmethod
    def _normalise_unicode(cls: type['PdfReport'], text: str) -> str:
        for src, dst in cls._UNICODE_LATEX_REPLACEMENTS.items():
            if src in text:
                text = text.replace(src, dst)
        return text

    @staticmethod
    def process_text_impl(text: str, unique_ids: set[str]) -> str:
        # Code spans are set aside first and put back last, fully escaped and breakable after
        # ``/ . _ : -``, so a path or a special character inside backticks prints literally and
        # cannot push past the margin.
        code_spans: list[str] = []

        def stash_code_span(match: "re.Match[str]") -> str:
            code_spans.append(match.group(1))
            return f"\x00{len(code_spans) - 1}\x00"

        text = re.sub(r"`([^`]*)`", stash_code_span, text)
        # A literal backslash (``C:\\Users``) is text, not the start of a LaTeX command.
        text = text.replace("\\", "\x01")
        text = PdfReport._normalise_unicode(text)

        # Escape literal ``{`` and ``}`` in the original user prose BEFORE any of the
        # substitutions below add their own (intentional, paired) braces. Subsequent
        # ``\hyperref{...}`` insertions are emitted post-escape, so their braces survive.
        # The trade-off is that a unique-ID surrounded by literal braces (e.g. ``{REQ-X}``)
        # loses its hyperref — extremely rare in practice and preferable to a
        # brace-mismatch crash inside pdflatex.
        text = text.replace("{", r"\{").replace("}", r"\}")

        # Replace any instances of a unique ID within the text to a link to the ID.
        # 1. Explode text into words
        words = text.split()

        # 2. For each word, check if it is a unique ID. unique_ids is a set, so this is O(1).
        # A linked ID sits in an \mbox, so it is never hyphenated across lines.
        for index, word in enumerate(words):
            leading = len(word) - len(word.lstrip("()[]{}.,;:<>"))
            trailing = len(word.rstrip("()[]{}.,;:<>"))
            prefix = word[:leading]
            suffix = word[trailing:]
            core = word[leading:trailing] if trailing > leading else word[leading:]
            altered_word = core.replace(":", "")
            if altered_word in unique_ids and core:
                words[index] = f"{prefix}\\mbox{{\\hyperref[{altered_word}]{{\\textbf{{{core}}}}}}}{suffix}"

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

        # Escape LaTeX special characters that survive in raw user prose. We deliberately
        # do NOT escape ``\``, ``{``, ``}`` here because the substitutions above
        # (``\hyperref{...}``) intentionally emit those — escaping them would mangle the
        # commands. The chars below never appear in our generated LaTeX commands, so it
        # is safe to escape them globally.
        new_text = (
            new_text.replace(r"&", r"\&")
            .replace(r"_", r"\_")
            .replace(r"%", r"\%")
            .replace(r"#", r"\#")
            .replace(r"$", r"\$")
            .replace(r"^", r"\^{}")
            .replace(r"~", r"\textasciitilde{}")
        )

        new_text = new_text.replace("\x01", r"\textbackslash{}")

        # 5. Put the code spans back.
        return _CODE_SPAN_PLACEHOLDER.sub(
            lambda match: breakable_code(code_spans[int(match.group(1))], PdfReport._UNICODE_LATEX_REPLACEMENTS),
            new_text,
        )

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
                "\\fancyhead[R]{\\traceflowrightheaderwide}",
            ]
        )
        return lines

    @staticmethod
    def _end_a3_landscape_block() -> list[str]:
        return [
            "\\restoregeometry",
            "\\setlength{\\headwidth}{\\textwidth}",
            "\\fancyhead[L]{\\includegraphics[width=\\traceflowstandardlogowidth,keepaspectratio]{\\traceflowtopleftlogo}}",  # noqa E501
            "\\fancyhead[R]{\\traceflowrightheaderstd}",
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
            for _prob_name, prob_score in axis_labels:
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
        ])
        return "\n".join(lines) + "\n"

    def _risk_test_evidence_cell(self, text: str) -> str:
        """The Test Evidence cell: one ID per line when it is a list of IDs, else the prose."""
        parts = [part.strip().rstrip(".").strip() for part in re.split(r"[,;]", text)]
        parts = [part for part in parts if part]
        if parts and all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]*", part) for part in parts):
            # One ID per line. An ID too long for the cell breaks after a hyphen, never inside
            # a word (an \mbox would run past the cell at \small).
            cells = [
                f"\\hyperref[{part}]{{\\textbf{{{breakable(part)}}}}}" if part in self.unique_ids else breakable(part)
                for part in parts
            ]
            return " \\newline ".join(cells)
        return self.process_text(text)

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
        # The heading sits on the A3 page with its table, never alone at the foot of the
        # previous page. \small, not \footnotesize: the A3 page is often printed on A4.
        table_lines.extend(
            [
                "\\subsection{Risk Register}",
                "",
                "\\small",
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
            test_evidence_text = self._risk_test_evidence_cell(risk.attributes.get("test_evidence", ""))
            test_evidence_content = test_evidence_text if test_evidence_text else "-"
            test_evidence_cell = (
                f"\\parbox[t]{{{test_evidence_width}cm}}{{\\raggedright {test_evidence_content}}}"
            )
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
            command = "\\" + "sub" * (level - 1) + "section" + ("*" if item.get("_unnumbered") else "")
            return command + "{" + self.process_text(item["children"][0]["text"]) + "}"

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
            raw = item.get("text", "")
            if raw.strip().lower() in {"<br>", "<br/>", "<br />"}:
                return "\\\\ "
            # Anything else (``<docId>``, ``<script nomodule>``) is prose that happens to look
            # like a tag: print it literally instead of dropping it.
            return self.process_text(raw)

        def handle_image(item: dict) -> str:
            url = item["src"]
            sidecar = item.get("_sidecar") or {}
            alt_text_raw = item.get("alt", "")
            caption_raw = sidecar.get("caption") or ""
            if not caption_raw.strip():
                # Fall back to alt text, but skip filename-shaped alts (e.g. "screenshot-001.png")
                # which look like a caption to LaTeX but tell the reader nothing.
                if alt_text_raw and not self._FILENAME_ALT.fullmatch(alt_text_raw.strip()):
                    caption_raw = alt_text_raw

            requirement_ids = self._sidecar_requirement_ids(sidecar)
            caption_parts: list[str] = []
            if caption_raw.strip():
                caption_parts.append(self.process_text(caption_raw.strip()))
            if requirement_ids:
                caption_parts.append(
                    f"\\textit{{(evidence for {self.process_text(', '.join(requirement_ids))})}}"
                )
            if not caption_parts:
                caption_parts.append(r"\textcolor{red!75!black}{\textbf{[Caption missing]}}")

            # The height cap keeps a tall capture (a full-page labbook screenshot) and its
            # caption on one page; [H] keeps the figure inside its evidence block. The
            # assertion's source code is not printed: it was truncated or transpiled.
            latex = [
                '\n\\begin{figure}[H]',
                '\n\\centering',
                f'\n\\includegraphics[{self._FIGURE_SIZE}]{{{url}}}',
                "\n\\caption{" + " ".join(caption_parts) + "}",
            ]
            checked_at = str(sidecar.get("checkedAt") or "").strip()
            if checked_at:
                latex.append("\n\\par\\footnotesize Checked at " + breakable_code(checked_at) + "\\normalsize")
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
            pass_id = self._form_field_name()
            fail_id = self._form_field_name()
            skip_id = self._form_field_name()
            comment_id = self._form_field_name()
            # The minipage keeps the Pass/Fail/Skip row and its comments box on one page.
            return r"""
\noindent
\begin{minipage}{\linewidth}
\begin{Form}
\textbf{Pass} \CheckBox[name=""" + pass_id + r"""]{} \hspace{2cm} \textbf{Fail} \CheckBox[name=""" + fail_id + r"""]{} \hspace{2cm} \textbf{Skip} \CheckBox[name=""" + skip_id + r"""]{} \\
\vspace{0.05cm}
\textbf{Comments} \\
\TextField[name=""" + comment_id + r""", multiline=true, width=\linewidth, height=""" + self.manualtest_comment_height + r"""]{}
\end{Form}
\end{minipage}
        """  # noqa E501

        def handle_auto_test(item: dict) -> str:
            content = item.get("text", "")
            lines = [line.strip() for line in content.splitlines() if line.strip()]
            if not lines:
                raise ValueError("autotest blocks must include a test ID.")
            if len(lines) > 1:
                raise ValueError("autotest blocks support exactly one test ID.")
            return self._render_autotest_binding(lines[0])

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
            mermaid_code = item["text"]
            # Named after the diagram's source, so two builds of the same input name it alike.
            stem = "mermaid-" + hashlib.sha256(mermaid_code.encode("utf-8")).hexdigest()[:16]
            svg_path = stem + ".svg"
            mmd_path = stem + ".mmd"
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
                "codespan": lambda item: breakable_code(item["text"], self._UNICODE_LATEX_REPLACEMENTS),
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

    # ------------------------------------------------------------------ evidence records
    #
    # An ``autotest`` block binds one evidence record (a collected test result) to a test
    # shell. How a binding prints depends on the render options:
    #
    # - evidence_mode "inline" (default): the record is printed where it is bound, in the
    #   "legacy" layout (today's) or the "record" layout (contract section 3: an
    #   ``Evidence <id>`` header, one provenance line, description, assertions with their
    #   rows, output, figures). With ``dedupe_evidence`` only the record's home binding
    #   prints it in full; every other binding prints a one-line pointer.
    # - evidence_mode "reference": each binding prints one compact row (ID, status, kind,
    #   assertion captions, ``<volume> p.<page>``) from an evidence index.

    def _reset_evidence_state(self) -> None:
        self._records: dict[str, EvidenceRecord] = {}
        self._staged: dict[str, str] = {}
        # IDs printed in full, in print order, and the label each carries.
        self._full_rendered: list[str] = []
        self._labelled: dict[str, str] = {}
        # Evidence ID -> the shell that prints it in full (de-duplication).
        self._evidence_homes: dict[str, str] = {}
        self._current_shell_id: Optional[str] = None
        self._missing_index_ids: list[str] = []
        self._reference_rows_in_shell = 0
        self._warned_ids: set[str] = set()
        self._scope_cache: dict[str, set[str]] = {}
        self._tests_by_id = self.document.tests_by_id()
        self._requirements_by_id = self.document.requirements_by_id()
        self._bindings: Optional[dict[str, EvidenceBinding]] = None
        self._form_fields = 0

    def _resolve_result_dir(self, evidence_id: str) -> str:
        if not self.test_results_dir:
            raise RuntimeError(
                "autotest blocks require --test-results-dir to be set."
            )
        source_dir = os.path.join(self.test_results_dir, evidence_id)
        if not os.path.isdir(source_dir):
            raise FileNotFoundError(
                f"Autotest ID `{evidence_id}` not found in test results directory: {self.test_results_dir}"
            )
        return source_dir

    def _resolve_autotest_result_file(self, test_id: str) -> tuple[str, str]:
        source_dir = self._resolve_result_dir(test_id)
        result_file = os.path.join(source_dir, "result.md")
        if not os.path.isfile(result_file):
            raise FileNotFoundError(
                f"Autotest result markdown missing for `{test_id}`: {result_file}"
            )
        return source_dir, result_file

    def _load_record(self, evidence_id: str) -> EvidenceRecord:
        record = self._records.get(evidence_id)
        if record is None:
            record = load_evidence_record(self._resolve_result_dir(evidence_id), evidence_id)
            self._records[evidence_id] = record
        return record

    def _stage_result_dir(self, evidence_id: str) -> str:
        """Copy a result folder to ``autotest-results/`` (once per render); return its path."""
        staged = self._staged.get(evidence_id)
        if staged is not None:
            return staged
        source_dir = self._resolve_result_dir(evidence_id)
        staged = "autotest-results/" + self._sanitize_path_component(evidence_id)
        if os.path.isdir(staged):
            shutil.rmtree(staged)
        shutil.copytree(source_dir, staged)
        self._staged[evidence_id] = staged
        return staged

    def _warn_missing_fields(self, evidence_id: str, missing_fields: list[str]) -> str:
        """The stderr warning the VoxelFlow build gates on, and the red banner in the PDF."""
        if not missing_fields:
            return ""
        joined = ", ".join(missing_fields)
        if evidence_id not in self._warned_ids:
            self._warned_ids.add(evidence_id)
            sys.stderr.write(
                f"WARNING: autotest `{evidence_id}` is missing required fields: {joined}\n"
            )
        return (
            "\n\\par\\noindent\\textcolor{red!75!black}{\\textbf{"
            + f"Validation gap: this test is missing {joined}. "
            + "Update the test source so reviewers see what was asserted."
            + "}}\n"
        )

    def _evidence_label(self, evidence_id: str) -> str:
        """``\\label`` and named destination ``ev:<id>``, emitted once per PDF."""
        if evidence_id in self._labelled:
            return ""
        anchor = evidence_anchor(evidence_id)
        self._labelled[evidence_id] = anchor
        return f"\\phantomsection\\label{{{anchor}}}\\hypertarget{{{anchor}}}{{}}%\n"

    def _shell_requirement_scope(self, shell_id: Optional[str]) -> set[str]:
        """A shell's requirement IDs plus every ancestor (``Parent:``) of them."""
        if not shell_id:
            return set()
        cached = self._scope_cache.get(shell_id)
        if cached is not None:
            return cached
        scope: set[str] = set()
        test = self._tests_by_id.get(shell_id)
        stack = list(test.req_ids) if test is not None else []
        while stack:
            req_id = stack.pop()
            if req_id in scope:
                continue
            scope.add(req_id)
            requirement = self._requirements_by_id.get(req_id)
            if requirement is not None:
                stack.extend(requirement.parent_ids)
        self._scope_cache[shell_id] = scope
        return scope

    def _captions_for_shell(self, record: EvidenceRecord, shell_id: Optional[str]) -> list[str]:
        """Assertion captions that concern the shell's requirements (or name no requirement)."""
        scope = self._shell_requirement_scope(shell_id)
        captions: list[str] = []
        for assertion in record.assertions:
            if not assertion.requirement_ids or not scope or scope & set(assertion.requirement_ids):
                captions.append(assertion.caption)
        return [caption for caption in captions if caption.strip()]

    def _inline_evidence_homes(self) -> dict[str, str]:
        """Home shell of each bound record, for de-duplication in a single PDF.

        The home is the first shell (corpus order) whose requirements, or their ancestors,
        include a requirement the record's assertions name; otherwise the first binding.
        """
        homes: dict[str, str] = {}
        for evidence_id, binding in self._evidence_bindings().items():
            if not binding.shell_ids:
                continue
            asserted = set(self._load_record(evidence_id).requirement_ids)
            home = binding.shell_ids[0]
            for shell_id in binding.shell_ids:
                if asserted & self._shell_requirement_scope(shell_id):
                    home = shell_id
                    break
            homes[evidence_id] = home
        return homes

    def _claims_home(self, evidence_id: str) -> bool:
        """True if this binding prints the record in full (de-duplication)."""
        if evidence_id in self._labelled:
            return False
        home = self._evidence_homes.get(evidence_id)
        return home is None or home == self._current_shell_id

    def _render_autotest_binding(self, evidence_id: str) -> str:
        if self.evidence_mode == "reference":
            return self._render_evidence_reference(evidence_id)
        if self.dedupe_evidence and not self._claims_home(evidence_id):
            return self._render_evidence_pointer(evidence_id)
        if self.evidence_layout == "record":
            return self._render_evidence_record(evidence_id)
        if not os.path.isfile(os.path.join(self._resolve_result_dir(evidence_id), "result.md")):
            # A result.json-only folder has no Markdown to lay out the legacy way.
            return self._render_evidence_record(evidence_id)
        return self._render_autotest_result(evidence_id)

    # ---------------------------------------------------------------- legacy layout

    @staticmethod
    def _plain_text(node: dict) -> str:
        if node.get("type") in {"text", "codespan", "inline_html"}:
            return str(node.get("text", ""))
        return "".join(PdfReport._plain_text(child) for child in node.get("children", []) or [])

    @classmethod
    def _drop_description_tails(cls: type['PdfReport'], items: list[dict], description: str) -> list[dict]:
        """Drop the loose paragraph a multi-line ``- Description:`` bullet leaves behind.

        mistune ends the bullet at its first line break, so the rest of the description
        became a paragraph after the metadata. The full description is printed instead.
        """
        flat_description = " ".join(description.split())
        kept: list[dict] = []
        before_sections = True
        for item in items:
            if item.get("type") == "heading":
                before_sections = False
            if before_sections and item.get("type") == "paragraph" and flat_description:
                text = " ".join(cls._plain_text(item).split())
                if text and text in flat_description:
                    continue
            kept.append(item)
        return kept

    @classmethod
    def _drop_caption_paragraphs(cls: type['PdfReport'], items: list[dict]) -> list[dict]:
        """Remove the collector's ``**Caption:** ...`` line in front of each image.

        The figure already prints that caption; the extra paragraph was left orphaned at
        the foot of a page whenever the figure moved to the next one.
        """
        for item in items:
            for node in cls._iter_ast_nodes(item):
                children = node.get("children")
                if node.get("type") != "paragraph" or not children:
                    continue
                if not any(child.get("type") == "image" for child in children):
                    continue
                kept: list[dict] = []
                in_caption = False
                for child in children:
                    if child.get("type") == "strong" and cls._plain_text(child).strip().lower() == "caption:":
                        in_caption = True
                        continue
                    if child.get("type") == "image":
                        in_caption = False
                    if in_caption:
                        # The caption text, including inline HTML such as ``<docId>``.
                        continue
                    kept.append(child)
                node["children"] = kept
        return items

    def _render_autotest_result(self, test_id: str) -> str:
        source_dir, _ = self._resolve_autotest_result_file(test_id)
        staged_dir = self._stage_result_dir(test_id)
        autotest = load_autotest_result(source_dir, test_id)
        record = autotest.record
        # The typed description keeps inline HTML (``<docId>``) and all of a multi-line value.
        description = record.description if record is not None and record.description else autotest.description

        # Build the visible "Description" and "Assertions" blocks ahead of the raw bullets so a
        # reviewer reads intent before metadata. ``autotest.other_items`` keeps any sections
        # other than ``## Assertions`` (e.g. ``## Output``, ``## Screenshots``) in their original
        # order.
        other_items = self._drop_description_tails(autotest.other_items, description)
        other_items = self._drop_caption_paragraphs(other_items)
        metadata_items = self._style_autotest_statuses(autotest.metadata_items) if autotest.metadata_items else []
        ordered_items = self._normalise_autotest_headings(other_items)
        if self._toc_depth is not None:
            # With a chosen contents depth, evidence sub-headings are unnumbered and kept
            # out of the contents (``Output``/``Screenshots`` once filled 37 pages of it).
            for item in ordered_items:
                if item.get("type") == "heading":
                    item["_unnumbered"] = True
        ordered_items = self._prefix_image_sources(ordered_items, staged_dir)
        ordered_items = self._attach_image_sidecar_metadata(ordered_items, staged_dir)

        latex_fragments: list[str] = []
        if self.dedupe_evidence or self._label_records:
            latex_fragments.append(self._evidence_label(test_id))
        self._full_rendered.append(test_id)
        latex_fragments.append(self._warn_missing_fields(test_id, autotest.missing_fields))

        if description:
            latex_fragments.append(
                "\n\\subsubsection*{Description}\n"
                + self.process_text(description)
                + "\n"
            )

        if autotest.assertions_items:
            normalised_assertions = self._normalise_autotest_headings(
                [dict(item) for item in autotest.assertions_items]
            )
            latex_fragments.append("\n\\subsubsection*{Assertions}\n")
            latex_fragments.append(self.md_to_latex(normalised_assertions))

        if metadata_items:
            # Ragged right, so a long Source path breaks at its separators instead of
            # running past the margin.
            latex_fragments.append("{\\raggedright\n" + self.md_to_latex(metadata_items) + "\n\\par}")
        latex_fragments.append(self.md_to_latex(ordered_items))
        return "\n".join(fragment for fragment in latex_fragments if fragment)

    # ---------------------------------------------------------------- record layout

    @staticmethod
    def _href_url(url: str) -> str:
        """A URL safe inside ``\\href{...}``, even within another command's argument."""
        encoded = "".join(
            f"%{ord(ch):02X}" if ch in "{}\\^~ \"" or ord(ch) < 32 else ch for ch in url.strip()
        )
        return encoded.replace("%", "\\%").replace("#", "\\#")

    @staticmethod
    def _evidence_subheading(title: str, note: str = "") -> str:
        """An unnumbered sub-heading of a record: never numbered, never in the contents."""
        suffix = f" {{\\footnotesize\\mdseries {note}}}" if note else ""
        return (
            f"\\par\\needspace{{4\\baselineskip}}\\smallskip\\noindent\\textbf{{{title}}}{suffix}"
            "\\par\\nopagebreak\n"
        )

    def _evidence_id_text(self, evidence_id: str) -> str:
        return breakable(evidence_id)

    def _evidence_header(self, record: EvidenceRecord) -> str:
        evidence_id = record.evidence_id
        bookmark_text = latex_escape(f"Evidence {evidence_id}")
        return (
            "\\par\\needspace{8\\baselineskip}\\medskip\\noindent{\\color{gray!60}\\rule{\\linewidth}{0.6pt}}"
            "\\par\\nopagebreak\\noindent\n"
            + self._evidence_label(evidence_id)
            + f"\\pdfbookmark[3]{{{bookmark_text}}}{{bm-{evidence_anchor(evidence_id)}}}%\n"
            "\\begin{minipage}[t]{\\dimexpr\\linewidth-3.2cm\\relax}\\raggedright"
            f"\\textbf{{Evidence {self._evidence_id_text(evidence_id)}}}\\end{{minipage}}\\hfill"
            f"\\begin{{minipage}}[t]{{3cm}}\\raggedleft {coloured_status(record.status)}\\end{{minipage}}"
            "\\par\\nopagebreak\n"
        )

    def _evidence_provenance(self, record: EvidenceRecord) -> str:
        parts: list[str] = []
        if record.kind:
            parts.append(latex_escape(status_label(record.kind)))
        workflow = record.workflow_name or record.workflow_path
        if workflow:
            parts.append(latex_escape(workflow))
        if record.run_id is not None or record.run_url:
            run_text = f"run {record.run_id}" if record.run_id is not None else "run"
            if record.run_url:
                parts.append(f"\\href{{{self._href_url(record.run_url)}}}{{{run_text}}}")
            else:
                parts.append(run_text)
        if record.duration_text:
            parts.append(latex_escape(record.duration_text))
        if record.source:
            parts.append("Source " + breakable_code(record.source))
        if not parts:
            return ""
        separator = "\\enspace\\textbar\\enspace "
        return (
            "\\noindent{\\footnotesize\\raggedright\\color{black!75}" + separator.join(parts) + "\\par}\\nopagebreak\n"
        )

    def _requirement_list(self, requirement_ids: list[str]) -> str:
        return ", ".join(self.process_text(req_id) for req_id in requirement_ids)

    def _figure_label(self, evidence_id: str, position: int) -> str:
        return f"{FIGURE_LABEL_PREFIX}{evidence_anchor(evidence_id)[len(LABEL_PREFIX):]}:{position + 1}"

    def _rows_table(self, rows: list[list[str]]) -> str:
        cells_per_row = 3
        lines = [
            "\\par\\smallskip{\\footnotesize\\setlength{\\tabcolsep}{3pt}\\renewcommand{\\arraystretch}{1.05}%",
            "\\noindent\\begin{tabularx}{\\linewidth}{@{}"
            + ">{\\raggedright\\arraybackslash}X" * cells_per_row
            + "@{}}",
            "\\toprule",
            "\\textbf{Check} & \\textbf{Expected} & \\textbf{Actual} \\\\",
            "\\midrule",
        ]
        for row in rows:
            cells = [str(cell) for cell in row]
            if len(cells) > cells_per_row:
                cells = cells[:cells_per_row - 1] + [" ".join(cells[cells_per_row - 1:])]
            cells += [""] * (cells_per_row - len(cells))
            lines.append(" & ".join(breakable(PdfReport._normalise_unicode(cell)) for cell in cells) + " \\\\")
        lines += ["\\bottomrule", "\\end{tabularx}\\par}"]
        return "\n".join(lines) + "\n"

    def _evidence_assertions(self, record: EvidenceRecord) -> str:
        if not record.assertions:
            return ""
        lines = [self._evidence_subheading("Assertions"), "{\\raggedright", "\\begin{itemize}"]
        for assertion in record.assertions:
            caption = self.process_text(assertion.caption) if assertion.caption.strip() else "\\textit{(no caption)}"
            item = "\\item " + caption
            if assertion.requirement_ids:
                item += " {\\footnotesize(" + self._requirement_list(assertion.requirement_ids) + ")}"
            if assertion.figure is not None and 0 <= assertion.figure < len(record.figures):
                figure_label = self._figure_label(record.evidence_id, assertion.figure)
                item += f" {{\\footnotesize--- Figure~\\ref{{{figure_label}}}}}"
            elif assertion.checked_at:
                item += " {\\footnotesize--- checked at " + breakable_code(assertion.checked_at) + "}"
            lines.append(item)
            if assertion.rows:
                lines.append(self._rows_table(assertion.rows))
        lines.append("\\end{itemize}}")
        return "\n".join(lines) + "\n"

    def _evidence_output(self, record: EvidenceRecord) -> str:
        output = record.output
        if output.is_empty:
            return ""
        lines = list(output.lines)
        while lines and not lines[-1].strip():
            lines.pop()
        shown = len(lines)
        if output.truncated and output.total_lines:
            note = f"(last {shown} of {output.total_lines} lines)"
        else:
            note = f"({shown} line{'s' if shown != 1 else ''})"
        body = "\n".join(listing_ascii(line).replace("\\end{Verbatim}", "\\end {Verbatim}") for line in lines)
        return (
            self._evidence_subheading("Output", note)
            + "\\begin{Verbatim}[fontsize=\\footnotesize,breaklines=true,breakanywhere=true,"
            + "breaksymbolleft={\\textcolor{gray}{\\tiny$\\hookrightarrow$}}]\n"
            + body
            + "\n\\end{Verbatim}\n"
        )

    def _evidence_figures(self, record: EvidenceRecord, staged_dir: str) -> str:
        if not record.figures:
            return ""
        fragments: list[str] = []
        for position, figure in enumerate(record.figures):
            if self._is_external_reference(figure.path):
                continue
            relative = figure.path[2:] if figure.path.startswith("./") else figure.path
            staged_path = f"{staged_dir}/{relative}"
            if not os.path.isfile(staged_path):
                raise FileNotFoundError(
                    f"Evidence `{record.evidence_id}` figure `{figure.path}` is missing from its result folder"
                )
            caption = self.process_text(figure.caption.strip()) if figure.caption.strip() else (
                r"\textcolor{red!75!black}{\textbf{[Caption missing]}}"
            )
            if figure.requirement_ids:
                caption += f" \\textit{{(evidence for {self._requirement_list(figure.requirement_ids)})}}"
            if figure.short_sha256:
                caption += f". \\mbox{{SHA-256 \\texttt{{{figure.short_sha256}}}}}"
            lines = ["\\begin{figure}[H]"]
            if not fragments:
                # The sub-heading travels with the first figure, so it is never left alone at
                # the foot of a page.
                lines.append("{\\raggedright\\noindent\\textbf{Figures}\\par}\\smallskip")
            lines += [
                "\\centering",
                f"\\adjustbox{{{self._RECORD_FIGURE_BOX}}}{{\\includegraphics{{{staged_path}}}}}",
                "\\caption{" + caption + "}\\label{" + self._figure_label(record.evidence_id, position) + "}",
            ]
            if figure.checked_at:
                lines.append("{\\footnotesize Checked at " + breakable_code(figure.checked_at) + "\\par}")
            lines.append("\\end{figure}")
            fragments.append("\n".join(lines))
        return "\n".join(fragments) + "\n"

    def _shell_reference(self, shell_id: str, *, with_volume: bool = True) -> str:
        """A shell ID: a link where its heading is in this PDF, else bold (with its volume)."""
        if self._volume is None:
            return self.process_text(shell_id)
        plan, spec = self._volume
        placement = plan.shells.get(shell_id)
        if placement is not None and placement.volume_code == spec.code and shell_id in self._volume_shells:
            return f"\\mbox{{\\hyperref[vshell:{shell_id}]{{\\textbf{{{latex_escape(shell_id)}}}}}}}"
        volume_code = placement.volume_code if placement is not None else None
        suffix = f" (\\mbox{{{latex_escape(volume_code)}}})" if volume_code and with_volume else ""
        return f"\\mbox{{\\textbf{{{latex_escape(shell_id)}}}}}{suffix}"

    def _evidence_bound_by(self, evidence_id: str) -> str:
        binding = self._evidence_bindings().get(evidence_id)
        if binding is None or not binding.shell_ids:
            return ""
        shells = ", ".join(self._shell_reference(shell_id) for shell_id in binding.shell_ids)
        text = "\\textbf{Bound by:} " + shells
        if binding.requirement_ids:
            text += ". \\textbf{Requirements:} " + self._requirement_list(binding.requirement_ids)
        if binding.tier1_ids:
            text += ". \\textbf{Tier 1:} " + self._requirement_list(binding.tier1_ids)
        return "\\par\\smallskip\\noindent{\\footnotesize\\raggedright " + text + ".\\par}\n"

    def _evidence_bindings(self) -> dict[str, EvidenceBinding]:
        if self._bindings is None:
            hierarchy: Optional[HierarchyConfig] = self.document.hierarchy
            if self._volume is not None:
                hierarchy = self._volume[0].hierarchy
            elif self._report_plan is not None:
                hierarchy = self._report_plan.hierarchy
            self._bindings = self.document.evidence_bindings(hierarchy)
        return self._bindings

    def _render_evidence_record(self, evidence_id: str) -> str:
        """Contract section 3 layout. The record carries the ``ev:<id>`` label."""
        record = self._load_record(evidence_id)
        staged_dir = self._stage_result_dir(evidence_id)
        self._full_rendered.append(evidence_id)
        parts = [
            self._evidence_header(record),
            self._evidence_provenance(record),
            self._warn_missing_fields(evidence_id, record.missing_fields),
        ]
        if record.description.strip():
            parts.append(
                "\\par\\smallskip\\noindent{\\raggedright\\textbf{Description.} "
                + self.process_text(record.description.strip())
                + "\\par}\n"
            )
        parts.append(self._evidence_assertions(record))
        parts.append(self._evidence_output(record))
        parts.append(self._evidence_figures(record, staged_dir))
        if self.evidence_bound_by:
            parts.append(self._evidence_bound_by(evidence_id))
        return "".join(part for part in parts if part)

    def _home_description(self, evidence_id: str) -> str:
        """Where a pointer sends the reader: ``under TEST-X, p.~N`` or ``in <volume> under ...``."""
        anchor = evidence_anchor(evidence_id)
        if self._volume is not None:
            plan, spec = self._volume
            home = plan.evidence.get(evidence_id)
            if home is not None and home.volume_code != spec.code:
                where = (
                    f"in \\mbox{{{latex_escape(home.volume_code)}}} under "
                    + self._shell_reference(home.shell_id, with_volume=False)
                )
                entry = self.evidence_index.get(evidence_id)
                if entry is not None and str(entry.get("page", "")).strip():
                    where += f", p.~{latex_escape(str(entry['page']))}"
                return where
        home_shell = self._evidence_homes.get(evidence_id)
        where = f"under {self._shell_reference(home_shell)}, " if home_shell else ""
        return f"{where}p.~\\pageref{{{anchor}}}"

    def _render_evidence_pointer(self, evidence_id: str) -> str:
        """One line for a binding whose record is printed in full elsewhere."""
        record = self._load_record(evidence_id)
        self._warn_missing_fields(evidence_id, record.missing_fields)
        line = (
            "\\par\\smallskip\\noindent{\\small\\raggedright\\textbf{Evidence "
            + self._evidence_id_text(evidence_id)
            + "} ("
            + coloured_status(record.status)
            + "): full record "
            + self._home_description(evidence_id)
            + ".\\par}\n"
        )
        captions = self._captions_for_shell(record, self._current_shell_id)
        if captions:
            line += "{\\footnotesize\\begin{itemize}\n"
            line += "\n".join("\\item " + self.process_text(caption) for caption in captions)
            line += "\n\\end{itemize}}\n"
        return line

    # ---------------------------------------------------------------- reference rows

    _REFERENCE_COLUMNS = (
        "@{}>{\\raggedright\\arraybackslash}p{0.31\\linewidth}"
        ">{\\raggedright\\arraybackslash}p{1.3cm}"
        ">{\\raggedright\\arraybackslash}p{1.35cm}"
        ">{\\raggedright\\arraybackslash}X"
        ">{\\raggedleft\\arraybackslash}p{2.9cm}@{}"
    )

    def _render_evidence_reference(self, evidence_id: str) -> str:
        """``ID | status | kind | assertion captions | <volume> p.<page>`` from the evidence index."""
        entry = self.evidence_index.get(evidence_id)
        if entry is None:
            if evidence_id not in self._missing_index_ids:
                self._missing_index_ids.append(evidence_id)
            return ""
        record = self._load_record(evidence_id)
        indexed_status = str(entry.get("status", "")).strip().lower()
        if indexed_status != record.status.strip().lower():
            raise EvidenceIndexError(
                f"Evidence `{evidence_id}` is `{record.status}` in the test results but `{entry.get('status')}` "
                f"in the evidence index for {entry.get('volume')}; rebuild the evidence volumes"
            )
        self._warn_missing_fields(evidence_id, record.missing_fields)
        captions = [self.process_text(caption) for caption in self._captions_for_shell(record, self._current_shell_id)]
        if not captions:
            # None of the record's assertions names this shell's requirements: list them all,
            # with the requirements they do name, so the row still says what was asserted.
            captions = [
                self.process_text(assertion.caption)
                + (f" ({self._requirement_list(assertion.requirement_ids)})" if assertion.requirement_ids else "")
                for assertion in record.assertions
                if assertion.caption.strip()
            ]
        caption_cell = "\\newline ".join("\\textbullet~" + caption for caption in captions)
        where = f"\\mbox{{{latex_escape(str(entry['volume']))}}} \\mbox{{p.~{latex_escape(str(entry['page']))}}}"
        lines = [
            "\\par\\noindent{\\footnotesize\\setlength{\\tabcolsep}{3pt}\\renewcommand{\\arraystretch}{1.1}%",
            f"\\begin{{tabularx}}{{\\linewidth}}{{{self._REFERENCE_COLUMNS}}}",
        ]
        if self._reference_rows_in_shell == 0:
            lines += [
                "\\toprule",
                "\\textbf{Evidence} & \\textbf{Status} & \\textbf{Kind} & \\textbf{Asserted} & \\textbf{Record} \\\\",
                "\\midrule",
            ]
        else:
            lines.append("\\arrayrulecolor{gray!40}\\hline\\arrayrulecolor{black}")
        lines.append(
            " & ".join(
                [
                    self._evidence_id_text(evidence_id),
                    coloured_status(record.status),
                    latex_escape(status_label(record.kind)) if record.kind else "",
                    caption_cell,
                    where,
                ]
            )
            + " \\\\"
        )
        lines += ["\\end{tabularx}\\par}"]
        self._reference_rows_in_shell += 1
        return "\n".join(lines) + "\n"

    def _form_field_name(self) -> str:
        """A form-field name unique in the PDF and the same in every build of the same input."""
        self._form_fields += 1
        seed = f"{self.document.name}|{self.document.version}|{self._form_fields}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:10].upper()

    def _render_shell(self, test: "Test", *, command: str = "subsection") -> str:
        """A test shell: its heading (with the shell's label) and its content."""
        heading = "\\" + command + "{" + self.process_text(test.test_id + ": " + test.title) + "}"
        heading += "\\label{" + test.test_id + "}\n\n"
        self._current_shell_id = test.test_id
        self._reference_rows_in_shell = 0
        try:
            body = self.render_test_content(test.content)
        finally:
            self._current_shell_id = None
        return heading + body + "\n\n"

    def evidence_status(self, evidence_id: str) -> str:
        """A bound record's status: from its result folder, else the evidence index; ``""`` if unknown."""
        if self.test_results_dir:
            return self._load_record(evidence_id).status
        entry = self.evidence_index.get(evidence_id)
        return str(entry.get("status", "")) if entry is not None else ""

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
        manualtest_comment_height: str = "1.2cm",
    ):
        self.document = document
        self.top_left_logo_path = top_left_logo_path
        self.top_right_logo_path = top_right_logo_path
        self.manualtest_comment_height = manualtest_comment_height
        self.test_results_dir: Optional[str] = None
        if test_results_dir:
            resolved_test_results_dir = os.path.abspath(test_results_dir)
            if not os.path.isdir(resolved_test_results_dir):
                raise FileNotFoundError(f"Test results directory does not exist: {resolved_test_results_dir}")
            self.test_results_dir = resolved_test_results_dir
        self.unique_ids = self._collect_unique_ids()
        self.traceability_a3 = False
        # Evidence options; ``render`` and ``render_evidence_volume`` set them per call.
        self.evidence_layout = "legacy"
        self.evidence_mode = "inline"
        self.dedupe_evidence = False
        self.evidence_bound_by = False
        self.evidence_index: dict[str, dict] = {}
        # Label every record printed in full (for an evidence index or page references).
        self._label_records = False
        # (plan, volume spec) while an evidence volume renders.
        self._volume: Optional[tuple["VolumePlan", "VolumeSpec"]] = None
        self._volume_shells: set[str] = set()
        self._toc_depth: Optional[int] = None
        # The volume plan of a hierarchy report (``render(volume_plan=...)``).
        self._report_plan: Optional["VolumePlan"] = None
        self._reset_evidence_state()

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

    def _header_latex(
        self,
        *,
        report_title: str,
        include_cover_page: bool,
        include_toc: bool,
        cover_signature_boxes: bool = False,
        cover_no_tester: bool = False,
        cover_test_date: Optional[str] = None,
        cover_result: Optional[str] = None,
        cover_approved_usage: Optional[str] = None,
        cover_single_signoff: bool = False,
        no_right_logo: bool = False,
        document_code: Optional[str] = None,
        document_type: Optional[str] = None,
        toc_depth: Optional[int] = None,
        cover_subtitle: str = "",
        cover_rows: Optional[list[tuple[str, str]]] = None,
        cover_version: Optional[str] = None,
        cover_engineer: Optional[str] = None,
        generated_date_text: Optional[str] = None,
        footer_left: Optional[str] = None,
        after_cover: str = "",
        cover_note: str = "",
    ) -> tuple[str, str, str]:
        """The rendered ``report-header.tex``, and the left and right logo file names.

        ``cover_rows`` (label, LaTeX value) replaces the standard cover table and its
        sign-off blocks (an evidence volume is approved as part of its report).

        ``cover_version`` / ``cover_engineer``: ``None`` prints the row blank (today's cover,
        filled in by hand), ``""`` leaves the row out, any other text fills it.
        ``generated_date_text`` replaces LaTeX's ``\\today``; ``footer_left`` (LaTeX) replaces
        the footer's "<code> | Report produced using TraceFlow <version>"; ``after_cover``
        (LaTeX) is printed after the cover, before the contents; ``cover_note`` (LaTeX) under
        the cover's sign-off.
        """
        header = _latex_jinja2_env.from_string(
            load_resource("traceflow.res", "report-header.tex").decode("utf-8")
        )

        tex_vars: dict[str, object] = dict(self.get_global_tex_vars())
        tex_vars["report_title"] = self.process_text(report_title)
        tex_vars["include_cover_page"] = include_cover_page
        tex_vars["include_toc"] = include_toc
        tex_vars["cover_signature_boxes"] = cover_signature_boxes
        tex_vars["cover_no_tester"] = cover_no_tester
        tex_vars["cover_single_signoff"] = cover_single_signoff
        tex_vars["cover_test_date"] = self.process_text(cover_test_date) if cover_test_date else ""
        tex_vars["cover_result"] = self.process_text(cover_result) if cover_result else ""
        tex_vars["cover_approved_usage"] = self.process_text(cover_approved_usage) if cover_approved_usage else ""
        tex_vars["document_code"] = self.process_text(document_code or "N/A")
        tex_vars["document_type"] = self.process_text(document_type or "TraceFlow Report")
        tex_vars["toc_depth"] = toc_depth
        tex_vars["cover_subtitle"] = cover_subtitle
        tex_vars["cover_rows"] = list(cover_rows or [])
        tex_vars["record_layout"] = self.evidence_layout == "record"
        tex_vars["cover_version"] = self._cover_row_value(cover_version)
        tex_vars["cover_engineer"] = self._cover_row_value(cover_engineer)
        tex_vars["generated_date"] = latex_escape(generated_date_text) if generated_date_text else "\\today"
        tex_vars["footer_left"] = (
            footer_left
            if footer_left is not None
            else "\\traceflowdocumentcode{} | Report produced using TraceFlow " + __version__
        )
        tex_vars["after_cover"] = after_cover
        tex_vars["cover_note"] = cover_note
        top_left_logo_name = PdfReport._logo_basename(self.top_left_logo_path, "traceflow-logo.png")
        # An empty right-logo name suppresses the header's right slot entirely
        # (the template guards on it); otherwise it falls back to the bundled logo.
        top_right_logo_name = (
            "" if no_right_logo
            else PdfReport._logo_basename(self.top_right_logo_path, "voxelflow-logo.png")
        )

        tex_vars["top_left_logo"] = top_left_logo_name
        tex_vars["top_right_logo"] = top_right_logo_name
        return header.render(**tex_vars), top_left_logo_name, top_right_logo_name

    def _cover_row_value(self, value: Optional[str]) -> Optional[str]:
        """Template value of an optional cover row: ``""`` (blank row), ``None`` (no row), or text."""
        if value is None:
            return ""
        if not value.strip():
            return None
        return self.process_text(value.strip())

    def _release_footer(self, commit: str) -> str:
        """``<code> | <product> <version> (<commit12>) | TraceFlow <version>`` (TF-07)."""
        product = " ".join(part for part in (self.document.name, self.document.version) if part)
        if commit:
            product += f" ({commit[:12]})"
        return f"\\traceflowdocumentcode{{}} | {latex_escape(product)} | TraceFlow {latex_escape(__version__)}"

    @staticmethod
    def _reproducible(document: str) -> tuple[str, Optional[dict[str, str]]]:
        """With ``SOURCE_DATE_EPOCH`` set: a fixed PDF ID and date, so equal inputs give equal bytes.

        pdfTeX then stamps the creation date from ``SOURCE_DATE_EPOCH``; ``FORCE_SOURCE_DATE``
        makes ``\\today`` follow it too. The trailer ID is a hash of the LaTeX source (pdfTeX
        would otherwise derive it from the file name), and no ``PTEX.*`` file names are written.
        """
        epoch = source_date_epoch()
        if epoch is None:
            return document, None
        trailer_id = hashlib.sha256(f"{epoch}\n{document}".encode("utf-8")).hexdigest()[:32]
        prefix = f"\\pdftrailerid{{{trailer_id}}}\\pdfsuppressptexinfo=-1\n"
        return prefix + document, dict(os.environ, FORCE_SOURCE_DATE="1")

    def _compile_document(
        self,
        document: str,
        *,
        top_left_logo_name: str,
        top_right_logo_name: str,
        tex_name: Optional[str] = None,
    ) -> tuple[bytes, str]:
        """Write the .tex in the current directory, run pdflatex three times.

        Returns the PDF bytes and the file stem, whose ``.aux`` and ``.log`` hold the final
        pass's labels and warnings.
        """
        self._ensure_logo(
            filename=top_left_logo_name,
            resource_name="traceflow-logo.png",
            provided_path=self.top_left_logo_path,
        )
        if top_right_logo_name:
            self._ensure_logo(
                filename=top_right_logo_name,
                resource_name="voxelflow-logo.png",
                provided_path=self.top_right_logo_path,
            )

        # Recursively copy all files from the document.input_dir folder to the current folder
        for root, _, files in os.walk(self.document.input_dir):
            for filename in files:
                shutil.copy(os.path.join(root, filename), filename)

        output_filename = tex_name or self.get_temporary_filename(suffix=".tex")
        document, latex_env = self._reproducible(document)

        with open(output_filename, "w") as output_file:
            output_file.write(document)

        pdflatex_command = f"pdflatex -interaction=nonstopmode -halt-on-error {output_filename}"
        try:
            for _ in range(3):
                subprocess.check_output(
                    pdflatex_command,
                    shell=True,  # noqa: S602
                    stderr=subprocess.STDOUT,
                    universal_newlines=True,
                    errors="replace",
                    env=latex_env,
                )  # nopep8
        except subprocess.CalledProcessError as exc:
            print("Status : FAIL", exc.returncode, exc.output)
            raise exc

        stem = os.path.splitext(output_filename)[0]
        with open(stem + ".pdf", "rb") as out:
            return out.read(), stem

    @staticmethod
    def _read_text_if_exists(path: str) -> str:
        if not os.path.isfile(path):
            return ""
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read()

    def _check_references(self, stem: str, *, strict: bool) -> None:
        """Fail on an undefined reference: any with ``strict``, else any to an evidence record."""
        missing = undefined_references(self._read_text_if_exists(stem + ".log"), "" if strict else LABEL_PREFIX)
        if missing:
            raise ValueError(
                "pdflatex reports undefined references after the last pass: " + ", ".join(missing)
            )

    def _evidence_index_entries(self, stem: str, volume_code: str) -> dict[str, dict]:
        """``{id: {volume, page, status, ...}}`` for every labelled record, from the final .aux."""
        pages = parse_aux_pages(self._read_text_if_exists(stem + ".aux"))
        entries: dict[str, dict] = {}
        unplaced: list[str] = []
        for evidence_id in self._full_rendered:
            anchor = self._labelled.get(evidence_id)
            if anchor is None or evidence_id in entries:
                continue
            page = pages.get(anchor)
            if page is None:
                unplaced.append(evidence_id)
                continue
            record = self._records.get(evidence_id)
            entries[evidence_id] = {
                "volume": volume_code,
                "page": int(page) if page.isdigit() else page,
                "status": record.status if record is not None else "",
                "kind": record.kind if record is not None else "",
                "anchor": anchor,
                "shell": self._evidence_homes.get(evidence_id, ""),
                "figures": len(record.figures) if record is not None else 0,
            }
        if unplaced:
            raise ValueError(
                "The final pdflatex pass placed no label for evidence: " + ", ".join(unplaced)
            )
        return entries

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
        cover_no_tester: bool = False,
        cover_test_date: Optional[str] = None,
        cover_result: Optional[str] = None,
        cover_approved_usage: Optional[str] = None,
        cover_single_signoff: bool = False,
        no_right_logo: bool = False,
        document_code: Optional[str] = None,
        document_type: Optional[str] = None,
        traceability_a3: bool = False,
        evidence_layout: str = "legacy",
        evidence_mode: str = "inline",
        dedupe_evidence: bool = False,
        evidence_bound_by: bool = False,
        evidence_index: Optional[dict[str, dict]] = None,
        evidence_index_out: Optional[str] = None,
        toc_depth: Optional[int] = None,
        work_dir: Optional[str] = None,
        strict_references: bool = False,
        structure: str = "files",
        matrix_style: Optional[str] = None,
        volume_plan: Optional["VolumePlan"] = None,
        document_set: Optional[DocumentSet] = None,
        cover_version: Optional[str] = None,
        cover_engineer: Optional[str] = None,
        commit: Optional[str] = None,
        date_format: Optional[str] = None,
        risk_register_ref: Optional[str] = None,
    ) -> bytes:
        """Render the report. The defaults reproduce today's structure.

        Structure (all opt-in):

        - ``structure``: "files" (one section per source file, as today) or "hierarchy" (one
          chapter per Tier 1, see ``traceflow.report_structure``). The hierarchy needs a Tier 1
          prefix, from ``volume_plan`` or ``config.yml``; it always uses list matrices, a risk
          trace table instead of the register, and defaults ``toc_depth`` to 1.
        - ``matrix_style``: "tick" (A3 tick matrices, the files default) or "list" (a portrait
          longtable per requirement file).
        - ``volume_plan``: the evidence-volume plan; gives the hierarchy its Tier 1 order and
          each requirement its volume.
        - ``document_set``: the files issued with this report, printed as a table after the
          cover (checked against ``volume_plan``, ``evidence_index`` and ``commit``).
        - ``cover_version`` / ``cover_engineer``: ``None`` keeps the blank row, ``""`` drops it.
        - ``commit``: the product commit; the footer becomes "<code> | <name> <version>
          (<commit12>) | TraceFlow <version>" (the hierarchy uses that footer without it too).
        - ``date_format``: print the generation date with this strftime format (the hierarchy
          defaults to "%d %B %Y"); otherwise LaTeX's ``\\today``. ``SOURCE_DATE_EPOCH`` fixes
          the date and the PDF's ID and timestamps.
        - ``risk_register_ref``: where the risk trace table says the full register is.

        Evidence:

        - ``evidence_layout``: "legacy" (today's evidence blocks) or "record" (contract
          section 3 records).
        - ``evidence_mode``: "inline" (records printed where bound) or "reference" (one row
          per binding, citing ``<volume> p.<page>`` from ``evidence_index``; an ID missing
          from the index fails the render).
        - ``dedupe_evidence``: print each record in full once, at its home binding, and a
          one-line pointer at every other binding.
        - ``evidence_bound_by``: end each record-layout record with its "Bound by" list.
        - ``evidence_index_out``: write ``{id: {volume, page, status, ...}}`` for every
          record printed in full, read from the final pass (``volume`` is the document code).
        - ``toc_depth``: contents depth; also unnumbers headings (PDF bookmarks keep depth 3).
        - ``work_dir``: build folder (default ``./report``); the .tex is then ``traceflow.tex``.
        - ``strict_references``: fail on any undefined reference, not only evidence ones.
        """
        if evidence_layout not in EVIDENCE_LAYOUTS:
            raise ValueError(f"evidence_layout must be one of {EVIDENCE_LAYOUTS}, not `{evidence_layout}`")
        if evidence_mode not in EVIDENCE_MODES:
            raise ValueError(f"evidence_mode must be one of {EVIDENCE_MODES}, not `{evidence_mode}`")
        if evidence_mode == "reference" and evidence_index is None:
            raise ValueError("evidence_mode `reference` needs an evidence index")
        if toc_depth is not None and not 0 <= toc_depth <= 5:
            raise ValueError("toc_depth must be between 0 and 5")
        if structure not in STRUCTURES:
            raise ValueError(f"structure must be one of {STRUCTURES}, not `{structure}`")
        hierarchy_mode = structure == "hierarchy"
        if matrix_style is None:
            matrix_style = "list" if hierarchy_mode else "tick"
        if matrix_style not in MATRIX_STYLES:
            raise ValueError(f"matrix_style must be one of {MATRIX_STYLES}, not `{matrix_style}`")
        if hierarchy_mode and matrix_style != "list":
            raise ValueError("structure `hierarchy` always prints list matrices (its derived-requirements tables)")
        if hierarchy_mode and not (include_requirements and include_tests):
            raise ValueError("structure `hierarchy` needs the requirements and the tests")
        if hierarchy_mode and toc_depth is None:
            toc_depth = 1
        commit = check_commit(commit)
        if document_set is not None:
            problems = document_set_errors(
                document_set,
                volume_codes=[spec.code for spec in volume_plan.manifest.volumes] if volume_plan else None,
                index=evidence_index if evidence_mode == "reference" else None,
                commit=commit,
                report_code=document_code or "",
            )
            if problems:
                raise DocumentSetError(f"Document set {document_set.path} disagrees with this render:\n  - "
                                       + "\n  - ".join(problems))
        layout = None
        if hierarchy_mode:
            layout = plan_hierarchy(
                self.document,
                volume_plan.hierarchy if volume_plan is not None else None,
                tier1_order=volume_plan.manifest.tier1_order() if volume_plan is not None else None,
            )
        date_text = (
            generated_date(date_format or DEFAULT_DATE_FORMAT) if (date_format or hierarchy_mode) else None
        )
        footer = self._release_footer(commit) if (commit or hierarchy_mode) else None
        # Resolved now: the build runs inside the work directory.
        index_out_path = os.path.abspath(evidence_index_out) if evidence_index_out is not None else None
        original_unique_ids = self.unique_ids
        original_traceability_a3 = self.traceability_a3
        original_options = self._evidence_options()
        original_plan = self._report_plan
        selected_unique_ids = self._collect_unique_ids(
            include_risks=include_risks,
            include_requirements=include_requirements,
            include_tests=include_tests,
        )
        self.unique_ids = selected_unique_ids
        self.traceability_a3 = traceability_a3
        self.evidence_layout = evidence_layout
        self.evidence_mode = evidence_mode
        self.dedupe_evidence = dedupe_evidence and evidence_mode == "inline"
        self.evidence_bound_by = evidence_bound_by
        self.evidence_index = dict(evidence_index or {})
        # The hierarchy's evidence index appendix cites inline records by their ``ev:`` label.
        self._label_records = evidence_index_out is not None or (hierarchy_mode and evidence_mode == "inline")
        self._toc_depth = toc_depth
        self._report_plan = volume_plan
        self._reset_evidence_state()
        try:
            structure_latex = StructureRenderer(
                self,
                layout=layout,
                plan=volume_plan,
                document_set=document_set,
                report_code=document_code or (volume_plan.manifest.report_code if volume_plan else ""),
                commit=commit,
                result_text=cover_result or "",
                risk_register_ref=risk_register_ref or "",
            )
            after_cover = structure_latex.document_set_table(self.document.version) if document_set else ""
            cover_note = (
                "Approval of this report covers it and every document listed, with its SHA-256, in the "
                "document-set table that follows this page."
                if document_set and include_cover_page else ""
            )
            default_title = self.document.name + " " + self.document.version + ": Validation Pack"
            document, top_left_logo_name, top_right_logo_name = self._header_latex(
                report_title=report_title or default_title,
                include_cover_page=include_cover_page,
                include_toc=include_toc,
                cover_signature_boxes=cover_signature_boxes,
                cover_no_tester=cover_no_tester,
                cover_test_date=cover_test_date,
                cover_result=cover_result,
                cover_approved_usage=cover_approved_usage,
                cover_single_signoff=cover_single_signoff,
                no_right_logo=no_right_logo,
                document_code=document_code,
                document_type=document_type,
                toc_depth=toc_depth,
                cover_version=cover_version,
                cover_engineer=cover_engineer,
                generated_date_text=date_text,
                footer_left=footer,
                after_cover=after_cover,
                cover_note=cover_note,
            )

            build_dir = work_dir or "report"
            # Create the build directory ("report" by default) if it doesn't exist
            os.makedirs(build_dir, exist_ok=True)

            with isolated_filesystem(build_dir):
                if self.dedupe_evidence and include_tests:
                    self._evidence_homes = self._inline_evidence_homes()

                if hierarchy_mode:
                    document += structure_latex.front_matter(self.document.version)
                    include_risks_section = False
                    include_requirements_section = include_tests_section = False
                else:
                    include_risks_section = include_risks
                    include_requirements_section = include_requirements
                    include_tests_section = include_tests

                if include_design:
                    for design_doc in self.document.design_documents:
                        document += self.render_markdown_document(design_doc)

                if include_supplementary:
                    for supplementary_doc in self.document.supplementary_documents:
                        document += self.render_markdown_document(supplementary_doc)

                if include_risks_section:
                    for risk_page in self.document.risks:
                        document += self.render_risk_document(risk_page)

                if include_requirements_section:
                    for req_page in self.document.requirements:
                        section_label = self._build_label_from_text("requirements", req_page.title)
                        document += "\\section{" + self.process_text(req_page.title) + "}\\label{" + section_label + "}\n\n"  # noqa E501
                        document += self.md_to_latex(req_page.generic_content)

                        if matrix_style == "list":
                            document += structure_latex.list_matrix(req_page)
                        else:
                            document += self.build_traceability_matrix(req_page, link_tests=include_tests)

                        separator = "\\vspace{0.15cm}\n\\noindent\\rule{\\linewidth}{0.4pt}\n\\vspace{0.15cm}\n\n"
                        for requirement_index, requirement in enumerate(req_page.items):
                            document += "\\subsection{" + self.process_text(requirement.req_id + ": " + requirement.title) + "}"  # noqa E501
                            document += "\\label{" + requirement.req_id + "}\n\n"

                            # TF-06: one breakable, linked "Verified by:" line, not one run-on
                            # "Test ID:" line per shell.
                            verified_by = structure_latex.verified_by_line(requirement)
                            if verified_by:
                                document += verified_by + "\n"

                            document += self.md_to_latex(requirement.content)
                            document += "\n\n"
                            if requirement_index < len(req_page.items) - 1:
                                document += separator

                        document += "\\newpage\n\n"

                if include_tests_section:
                    for test_page in self.document.tests:
                        section_label = self._build_label_from_text("tests", test_page.title)
                        document += "\\section{" + self.process_text(test_page.title) + "}\\label{" + section_label + "}\n\n"  # noqa E501
                        document += self.md_to_latex(test_page.generic_content)

                        separator = "\\vspace{0.15cm}\n\\noindent\\rule{\\linewidth}{0.4pt}\n\\vspace{0.15cm}\n\n"
                        for test_index, test in enumerate(test_page.items):
                            document += self._render_shell(test)
                            if test_index < len(test_page.items) - 1:
                                document += separator

                        document += "\\newpage\n\n"

                if hierarchy_mode:
                    document += structure_latex.chapters()
                    document += structure_latex.procedural_chapter()
                    if include_risks:
                        document += structure_latex.risk_trace()
                    document += structure_latex.area_appendix()
                    document += structure_latex.evidence_index_appendix()

                if self._missing_index_ids:
                    raise EvidenceIndexError(
                        "These evidence IDs are bound in the report but are in no evidence index "
                        "(--evidence-index): " + ", ".join(self._missing_index_ids)
                    )

                document += r"%%%%%%%%%%% END DOCUMENT" + "\n\n" r"\label{LastPage}" + "\n\n" + r"\end{document}" + "\n"

                pdf_bytes, stem = self._compile_document(
                    document,
                    top_left_logo_name=top_left_logo_name,
                    top_right_logo_name=top_right_logo_name,
                    tex_name="traceflow.tex" if work_dir else None,
                )
                if strict_references or self.dedupe_evidence:
                    self._check_references(stem, strict=strict_references)
                if index_out_path is not None:
                    write_evidence_index(index_out_path, self._evidence_index_entries(stem, document_code or ""))
                return pdf_bytes
        finally:
            self.unique_ids = original_unique_ids
            self.traceability_a3 = original_traceability_a3
            self._report_plan = original_plan
            self._restore_evidence_options(original_options)

    def _evidence_options(self) -> tuple:
        return (
            self._toc_depth,
            self.evidence_layout,
            self.evidence_mode,
            self.dedupe_evidence,
            self.evidence_bound_by,
            self.evidence_index,
            self._label_records,
            self._volume,
            self._volume_shells,
        )

    def _restore_evidence_options(self, options: tuple) -> None:
        (
            self._toc_depth,
            self.evidence_layout,
            self.evidence_mode,
            self.dedupe_evidence,
            self.evidence_bound_by,
            self.evidence_index,
            self._label_records,
            self._volume,
            self._volume_shells,
        ) = options

    # ------------------------------------------------------------------ evidence volumes

    @staticmethod
    def volume_short_code(report_code: str, volume_code: str, index: int) -> str:
        """``BXD-VF-019-E03`` -> ``E03`` (``E<nn>`` from the position otherwise)."""
        prefix = report_code + "-"
        if report_code and volume_code.startswith(prefix) and len(volume_code) > len(prefix):
            return volume_code[len(prefix):]
        return f"E{index:02d}"

    def _toc_heading(self, level: str, text: str, *, label: str = "", new_page: bool = False) -> str:
        """An unnumbered heading with a contents entry and a bookmark."""
        latex_text = self.process_text(text)
        plain = latex_escape(text)
        command = {"section": "section*", "subsection": "subsection*"}[level]
        lines = ["\\clearpage" if new_page else "", f"\\{command}{{{latex_text}}}", "\\phantomsection"]
        if label:
            lines.append(f"\\label{{{label}}}")
        lines.append(f"\\addcontentsline{{toc}}{{{level}}}{{\\texorpdfstring{{{latex_text}}}{{{plain}}}}}")
        return "\n".join(line for line in lines if line) + "\n"

    def _volume_how_to_read(self, plan: "VolumePlan", spec: "VolumeSpec", version: str, record_count: int) -> str:
        report_code = latex_escape(plan.manifest.report_code)
        report = f"{report_code} {latex_escape(version)}".strip()
        count = len(plan.manifest.volumes)
        scope = ", ".join(f"\\mbox{{{latex_escape(tier1_id)}}}" for tier1_id in spec.tier1_ids)
        records = f"{record_count} record{'s' if record_count != 1 else ''}"
        return (
            self._toc_heading("section", "How to read this volume")
            + f"This is evidence volume {spec.index} of {count} of {report}"
            + (f", {latex_escape(plan.manifest.report_title)}" if plan.manifest.report_title else "")
            + f". It covers Tier 1 {scope}, and holds every evidence record whose home is a test shell "
            + f"under {'them' if len(spec.tier1_ids) > 1 else 'it'}, each exactly once: {records}.\n\n"
            + "\\textbf{Where the rest is.} The requirements, the test shells (their description, steps and "
            + f"expected results) and the release verdict are in {report}. Here a shell appears only as a "
            + "heading: its ID, its title and the requirements it verifies. "
            + f"{report_code} cites each record as ``{latex_escape(spec.code)} p.~N''; the record index "
            + "gives the same pages.\n\n"
            + "\\textbf{How a record reads.}\n"
            + "\\begin{itemize}\n"
            + "\\item \\textbf{Header:} \\textbf{Evidence} and the record ID, with its status on the right. "
            + "Each record is also a named PDF destination, \\texttt{ev:<ID>}.\n"
            + "\\item \\textbf{Provenance line:} kind, CI workflow, CI run (linked when it has a URL), "
            + "duration and the test's source.\n"
            + "\\item \\textbf{Description:} what the test does.\n"
            + "\\item \\textbf{Assertions:} each check the test recorded as passing, the requirements it "
            + "covers and, where recorded, a table of check, expected and actual values.\n"
            + "\\item \\textbf{Output:} the console output, only when the test printed some.\n"
            + "\\item \\textbf{Figures:} the screenshots and images the test saved.\n"
            + "\\item \\textbf{Bound by:} every test shell, requirement and Tier 1 that uses the record. "
            + "A shell in another volume is shown with that volume's code.\n"
            + "\\end{itemize}\n\n"
            + "\\textbf{Figures.} Each figure is the original image collected from CI, embedded unaltered at "
            + "its native resolution and only scaled to fit the page. Its caption ends with the first 12 "
            + "hexadecimal characters of the SHA-256 of the original file, so the image can be checked "
            + "against the CI artifact.\n\n"
            + "\\textbf{Records bound more than once.} A record used by several shells is printed in full "
            + "once, under its home shell. Every other shell that binds it shows a one-line pointer: to the "
            + "page in this volume, or to the volume that holds the record.\n\n"
            + f"\\textbf{{Approval.}} This volume has no signature block. It is approved as part of {report}, "
            + "whose document-set table lists this file's SHA-256.\n\n"
            + "\\clearpage\n"
        )

    def _volume_record_index(self, plan: "VolumePlan", home_ids: list[str]) -> str:
        columns = (
            "@{}>{\\raggedright\\arraybackslash}p{0.43\\linewidth}"
            ">{\\raggedright\\arraybackslash}p{1.45cm}"
            ">{\\raggedright\\arraybackslash}p{0.31\\linewidth}"
            ">{\\raggedleft\\arraybackslash}p{0.9cm}@{}"
        )
        header = "\\textbf{Evidence ID} & \\textbf{Status} & \\textbf{Bound by} & \\textbf{Page} \\\\"
        lines = [
            self._toc_heading("section", "Record index"),
            "{\\footnotesize\\setlength{\\tabcolsep}{3pt}\\renewcommand{\\arraystretch}{1.1}",
            f"\\begin{{longtable}}{{{columns}}}",
            "\\toprule", header, "\\midrule", "\\endfirsthead",
            "\\toprule", header, "\\midrule", "\\endhead",
            "\\bottomrule", "\\endlastfoot",
        ]
        for evidence_id in home_ids:
            record = self._load_record(evidence_id)
            anchor = evidence_anchor(evidence_id)
            binding = plan.evidence[evidence_id].binding
            shells = ", ".join(self._shell_reference(shell_id) for shell_id in binding.shell_ids)
            lines.append(
                f"\\hyperref[{anchor}]{{{self._evidence_id_text(evidence_id)}}} & {coloured_status(record.status)} & "
                f"{shells} & \\pageref{{{anchor}}} \\\\"
            )
        lines += ["\\end{longtable}}", "\\clearpage"]
        return "\n".join(lines) + "\n"

    def _volume_body(self, plan: "VolumePlan", spec: "VolumeSpec") -> str:
        fragments: list[str] = []
        for group in plan.layout(spec.code):
            title = f"{group.tier1_id}: {group.title}" if group.title else group.tier1_id
            fragments.append(self._toc_heading("section", title, label=f"vtier1:{group.tier1_id}", new_page=True))
            shells = [shell for shell in group.shells if shell.evidence_ids]
            if not shells:
                fragments.append("No evidence record is bound under this Tier 1.\n")
            for shell in shells:
                heading = f"{shell.shell_id}: {shell.title}" if shell.title else shell.shell_id
                fragments.append(self._toc_heading("subsection", heading, label=f"vshell:{shell.shell_id}"))
                if shell.requirement_ids:
                    fragments.append(
                        "\\noindent{\\footnotesize\\raggedright Verifies: "
                        + self._requirement_list(shell.requirement_ids)
                        + ".\\par}\\nopagebreak\n"
                    )
                self._current_shell_id = shell.shell_id
                for evidence_id in shell.evidence_ids:
                    if evidence_id in plan.evidence:
                        fragments.append(self._render_autotest_binding(evidence_id))
                self._current_shell_id = None
        return "\n".join(fragments)

    def render_evidence_volume(
        self,
        plan: "VolumePlan",
        volume: str,
        *,
        report_title: Optional[str] = None,
        document_code: Optional[str] = None,
        document_type: Optional[str] = None,
        include_toc: bool = True,
        toc_depth: int = 2,
        no_right_logo: bool = False,
        cover_version: Optional[str] = None,
        evidence_index: Optional[dict[str, dict]] = None,
        evidence_index_out: Optional[str] = None,
        work_dir: Optional[str] = None,
        strict_references: bool = False,
        commit: Optional[str] = None,
        date_format: Optional[str] = None,
    ) -> tuple[bytes, dict[str, dict]]:
        """Render one evidence volume of ``plan`` and return the PDF and its evidence index.

        The volume holds a cover (no signature block), a how-to-read page, a record index
        and, per Tier 1 and per shell heading, every record homed in the volume, once, in the
        record layout with its "Bound by" list. Other bindings print a pointer. REQ and TEST
        IDs print as bold text, since the volume does not contain their sections.

        ``evidence_index`` (optional, other volumes' indexes) adds page numbers to pointers into
        other volumes. ``evidence_index_out`` writes this volume's index JSON. The footer reads
        "<code> | <name> <version> (<commit12>) | TraceFlow <version>" and the cover's date
        uses ``date_format`` (default "%d %B %Y"), honouring ``SOURCE_DATE_EPOCH``.
        """
        spec = plan.manifest.volume(volume)
        commit = check_commit(commit)
        date_text = generated_date(date_format or DEFAULT_DATE_FORMAT)
        index_out_path = os.path.abspath(evidence_index_out) if evidence_index_out is not None else None
        report_code = plan.manifest.report_code
        version = self.document.version
        original_unique_ids = self.unique_ids
        original_options = self._evidence_options()
        self.unique_ids = set()
        self.evidence_layout = "record"
        self.evidence_mode = "inline"
        self.dedupe_evidence = True
        self.evidence_bound_by = True
        self.evidence_index = dict(evidence_index or {})
        self._label_records = True
        self._volume = (plan, spec)
        self._reset_evidence_state()
        self._evidence_homes = {evidence_id: home.shell_id for evidence_id, home in plan.evidence.items()}
        layout = plan.layout(spec.code)
        self._volume_shells = {shell.shell_id for group in layout for shell in group.shells if shell.evidence_ids}
        home_ids = plan.home_evidence_ids(spec.code)
        try:
            records = [self._load_record(evidence_id) for evidence_id in home_ids]
            passed = sum(1 for record in records if record.passed)
            count = len(plan.manifest.volumes)
            short_code = self.volume_short_code(report_code, spec.code, spec.index)
            report = f"{report_code} {version}".strip()
            title = report_title or (
                f"{self.document.name} {version}: Traceability Evidence Volume {short_code}: {spec.title}"
            )
            tier1_scope = "\\newline ".join(
                self.process_text(f"{tier1_id}: {plan.tier1_titles.get(tier1_id, '')}".rstrip(": "))
                for tier1_id in spec.tier1_ids
            )
            cover_rows = [
                ("Part of", latex_escape(report)),
                ("Volume", f"{spec.index} of {count}"),
                ("Version under test", self.process_text(cover_version or f"{self.document.name} {version}".strip())),
                ("Tier 1 scope", tier1_scope),
                (
                    "Result",
                    f"{passed} of {len(records)} records in this volume passed. "
                    f"The release verdict is in \\mbox{{{latex_escape(report_code)}}}.",
                ),
                ("Initial Report Generation Date", latex_escape(date_text)),
                (
                    "Approval",
                    f"Approved as part of \\mbox{{{latex_escape(report)}}}; no separate signature. This file's "
                    f"SHA-256 is listed in the document-set table of \\mbox{{{latex_escape(report_code)}}}.",
                ),
            ]
            document, top_left_logo_name, top_right_logo_name = self._header_latex(
                report_title=title,
                include_cover_page=True,
                include_toc=include_toc,
                no_right_logo=no_right_logo,
                document_code=document_code or spec.code,
                document_type=document_type or "Traceability Evidence Volume",
                toc_depth=toc_depth,
                cover_subtitle=latex_escape(f"Evidence volume {spec.index} of {count}, part of {report}"),
                cover_rows=cover_rows,
                generated_date_text=date_text,
                footer_left=self._release_footer(commit),
            )

            build_dir = work_dir or "report"
            os.makedirs(build_dir, exist_ok=True)
            with isolated_filesystem(build_dir):
                document += self._volume_how_to_read(plan, spec, version, len(home_ids))
                document += self._volume_record_index(plan, home_ids)
                document += self._volume_body(plan, spec)
                document += r"%%%%%%%%%%% END DOCUMENT" + "\n\n" r"\label{LastPage}" + "\n\n" + r"\end{document}" + "\n"

                if sorted(self._full_rendered) != sorted(home_ids):
                    printed = set(self._full_rendered)
                    raise ValueError(
                        f"Volume {spec.code} must print each of its {len(home_ids)} home records once; "
                        f"missing {sorted(set(home_ids) - printed)}, "
                        f"repeated {sorted({e for e in self._full_rendered if self._full_rendered.count(e) > 1})}"
                    )
                pdf_bytes, stem = self._compile_document(
                    document,
                    top_left_logo_name=top_left_logo_name,
                    top_right_logo_name=top_right_logo_name,
                    tex_name="traceflow.tex" if work_dir else None,
                )
                self._check_references(stem, strict=strict_references)
                entries = self._evidence_index_entries(stem, spec.code)
                if index_out_path is not None:
                    write_evidence_index(index_out_path, entries)
                return pdf_bytes, entries
        finally:
            self.unique_ids = original_unique_ids
            self._restore_evidence_options(original_options)

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
            pdf_bytes = self.render(
                report_title=title,
                include_cover_page=False,
                include_design=spec.flags["include_design"],
                include_supplementary=spec.flags["include_supplementary"],
                include_risks=spec.flags["include_risks"],
                include_requirements=spec.flags["include_requirements"],
                include_tests=spec.flags["include_tests"],
            )
            with open(output_path, "wb") as f:
                f.write(pdf_bytes)
            output_files.append(output_path)
        return output_files
