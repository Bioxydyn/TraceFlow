"""LaTeX for the signed report's structure options.

``StructureRenderer`` builds, from a ``PdfReport``:

- the "Verified by" line under a requirement (TF-06, every render);
- the list-style traceability matrix, a portrait longtable per requirement file (TF-04).
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from traceflow.evidence_render import breakable
from traceflow.parser import Requirement, RequirementDocument
from traceflow.report_structure import shell_has_manual_test

if TYPE_CHECKING:  # pragma: no cover - typing only
    from traceflow.pdf_generator import PdfReport

_PASSED = {"passed", "pass", "success", "succeeded"}
_FAILED_COLOUR = "red!70!black"
_PASSED_COLOUR = "green!50!black"


def _column(fraction: float, *, align: str = "raggedright") -> str:
    return f">{{\\{align}\\arraybackslash}}p{{\\dimexpr {fraction:.3f}\\linewidth-2\\tabcolsep\\relax}}"


def longtable(headers: list[str], fractions: list[float], rows: list[str], *, size: str = "footnotesize",
              aligns: Optional[list[str]] = None) -> str:
    """A portrait longtable whose header repeats on every page; ``rows`` are complete rows."""
    spec = "".join(
        _column(fraction, align=(aligns[position] if aligns else "raggedright"))
        for position, fraction in enumerate(fractions)
    )
    header = " & ".join(f"\\textbf{{{text}}}" for text in headers) + " \\\\"
    lines = [
        f"{{\\{size}\\setlength{{\\tabcolsep}}{{3pt}}\\renewcommand{{\\arraystretch}}{{1.15}}%",
        "\\setlength{\\LTleft}{0pt}\\setlength{\\LTright}{0pt}%",
        f"\\begin{{longtable}}{{{spec}}}",
        "\\toprule", header, "\\midrule", "\\endfirsthead",
        f"\\multicolumn{{{len(headers)}}}{{@{{}}l}}{{\\textit{{(continued)}}}} \\\\",
        "\\toprule", header, "\\midrule", "\\endhead",
        "\\bottomrule", "\\endlastfoot",
    ]
    lines += rows
    lines += ["\\end{longtable}\\par}"]
    return "\n".join(lines) + "\n"


class StructureRenderer:
    """Builds the structure LaTeX for one render of ``report``."""

    def __init__(self, report: "PdfReport", *, report_code: str = "") -> None:
        self.report = report
        self.document = report.document
        self.report_code = report_code
        self.tests = self.document.tests_by_id()

    # ------------------------------------------------------------------ small pieces

    def id_link(self, item_id: str) -> str:
        """A bold ID that may break after its hyphens; a link where the ID is in this PDF."""
        text = f"\\textbf{{{breakable(item_id)}}}"
        if item_id in self.report.unique_ids:
            return f"\\hyperref[{item_id}]{{{text}}}"
        return text

    def id_list(self, item_ids: list[str], *, empty: str = "--", separator: str = ", ") -> str:
        if not item_ids:
            return empty
        return separator.join(self.id_link(item_id) for item_id in item_ids)

    def evidence_of_shells(self, shell_ids: list[str]) -> list[str]:
        found: list[str] = []
        for shell_id in shell_ids:
            test = self.tests.get(shell_id)
            for evidence_id in test.evidence_ids if test is not None else []:
                if evidence_id not in found:
                    found.append(evidence_id)
        return found

    def evidence_of_requirement(self, requirement: Requirement) -> list[str]:
        return self.evidence_of_shells(requirement.test_ids)

    def tally_cell(self, shell_ids: list[str]) -> str:
        """``passed/total`` for the records the shells bind; ``N/A (...)`` when they bind none."""
        evidence_ids = self.evidence_of_shells(shell_ids)
        if not evidence_ids:
            if not shell_ids:
                return "--"
            manual = any(shell_has_manual_test(self.tests[s]) for s in shell_ids if s in self.tests)
            return "N/A (manual)" if manual else "N/A (procedural)"
        statuses = [self.report.evidence_status(evidence_id) for evidence_id in evidence_ids]
        if any(not status for status in statuses):
            return f"{len(evidence_ids)} bound"
        passed = sum(1 for status in statuses if status.strip().lower() in _PASSED)
        colour = _PASSED_COLOUR if passed == len(statuses) else _FAILED_COLOUR
        return f"\\textcolor{{{colour}}}{{\\textbf{{{passed}/{len(statuses)}}}}}"

    def verified_by_line(self, requirement: Requirement, *, always: bool = False) -> str:
        """TF-06: one ``Verified by:`` line, breakable and linked, with the record count."""
        if not requirement.test_ids and not always:
            return ""
        if not requirement.test_ids:
            shells = f"\\textcolor{{{_FAILED_COLOUR}}}{{\\textbf{{no test shell}}}}"
        else:
            shells = self.id_list(requirement.test_ids)
        count = len(self.evidence_of_requirement(requirement))
        suffix = f" ({count} evidence record{'s' if count != 1 else ''})" if count else ""
        return f"{{\\raggedright\\noindent\\textbf{{Verified by:}} {shells}{suffix}\\par}}\n"

    @staticmethod
    def _row(cells: list[str], *, highlight: bool = False) -> str:
        prefix = "\\rowcolor{red!18}" if highlight else ""
        return prefix + " & ".join(cells) + " \\\\"

    # ------------------------------------------------------------------ TF-04 list matrix

    def list_matrix(self, req_page: RequirementDocument) -> str:
        """The requirement file's matrix as a portrait longtable (no A3 page, no page break)."""
        rows: list[str] = []
        for requirement in req_page.items:
            rows.append(
                self._row(
                    [
                        self.id_link(requirement.req_id),
                        self.report.process_text(requirement.title),
                        self.id_list(requirement.parent_ids),
                        self.id_list(requirement.test_ids),
                        self.tally_cell(requirement.test_ids),
                        self.id_list(requirement.risk_ids),
                    ],
                    highlight=not requirement.test_ids,
                )
            )
        return (
            "\\subsection{Traceability Matrix}\n\n"
            + longtable(
                ["Requirement", "Title", "Parent", "Verified by", "Evidence", "Risks"],
                [0.20, 0.25, 0.14, 0.21, 0.09, 0.11],
                rows,
            )
            + "\n"
        )
