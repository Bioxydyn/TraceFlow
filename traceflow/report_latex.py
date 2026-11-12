"""LaTeX for the signed report's structure options.

``StructureRenderer`` builds, from a ``PdfReport`` and the layout of
``traceflow.report_structure``:

- the "Verified by" line under a requirement (TF-06, every render);
- the list-style traceability matrix, a portrait longtable per requirement file (TF-04);
- for ``--structure hierarchy``: the front matter (how to read, release results, CI run
  register, records per volume, IUS to Tier 1), one chapter per Tier 1 with its
  derived-requirements table, the procedural and out-of-hierarchy chapters, the risk trace
  table and the appendices (requirements by area, evidence index);
- the document-set table printed on page 2.
"""
from __future__ import annotations

import copy
import re
from typing import TYPE_CHECKING, Optional

from traceflow.evidence_render import breakable, coloured_status, evidence_anchor, latex_escape
from traceflow.parser import Requirement, RequirementDocument, Test
from traceflow.report_structure import (
    DocumentSet,
    HierarchyLayout,
    RequirementSection,
    Tier1Chapter,
    ci_run_register,
    requirement_volumes,
    shell_has_manual_test,
    short_volume_code,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from traceflow.pdf_generator import PdfReport
    from traceflow.volumes import VolumePlan

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
        # The header sets no space around a longtable; without some, the top rule touches the
        # descenders of the paragraph above it.
        "\\setlength{\\LTpre}{\\smallskipamount}\\setlength{\\LTpost}{\\smallskipamount}%",
        f"\\begin{{longtable}}{{{spec}}}",
        "\\toprule", header, "\\midrule", "\\endfirsthead",
        f"\\multicolumn{{{len(headers)}}}{{@{{}}l}}{{\\textit{{(continued)}}}} \\\\",
        "\\toprule", header, "\\midrule", "\\endhead",
        "\\bottomrule", "\\endlastfoot",
    ]
    lines += rows
    lines += ["\\end{longtable}\\par}"]
    return "\n".join(lines) + "\n"


def row_tables(headers: list[str], fractions: list[float], rows: list[str], *, size: str = "footnotesize") -> str:
    """A table set as one ``tabular`` per row, its header once: it breaks between rows like a
    longtable, but it can follow a ``\\needspace`` page break (a longtable started just after
    one printed its "(continued)" head above the chapter heading)."""
    spec = "".join(_column(fraction) for fraction in fractions)
    header = " & ".join(f"\\textbf{{{text}}}" for text in headers) + " \\\\"
    lines = [
        f"\\par\\smallskip{{\\{size}\\setlength{{\\tabcolsep}}{{3pt}}\\renewcommand{{\\arraystretch}}{{1.15}}"
        "\\setlength{\\parskip}{0pt}%",
        f"\\noindent\\begin{{tabular}}{{@{{}}{spec}@{{}}}}\\toprule {header} \\midrule\\end{{tabular}}"
        "\\par\\nopagebreak",
    ]
    for position, row in enumerate(rows):
        rule = "\\bottomrule" if position == len(rows) - 1 else ""
        lines.append(f"\\noindent\\begin{{tabular}}{{@{{}}{spec}@{{}}}}{row}{rule}\\end{{tabular}}\\par")
    lines.append("}\\smallskip\n")
    return "\n".join(lines) + "\n"


class StructureRenderer:
    """Builds the structure LaTeX for one render of ``report``."""

    def __init__(
        self,
        report: "PdfReport",
        *,
        layout: Optional[HierarchyLayout] = None,
        plan: Optional["VolumePlan"] = None,
        document_set: Optional[DocumentSet] = None,
        report_code: str = "",
        commit: str = "",
        result_text: str = "",
        risk_register_ref: str = "",
    ) -> None:
        self.report = report
        self.document = report.document
        self.layout = layout
        self.plan = plan
        self.document_set = document_set
        self.report_code = report_code
        self.commit = commit
        self.result_text = result_text
        self.risk_register_ref = risk_register_ref
        self.tests = self.document.tests_by_id()
        self.requirements = self.document.requirements_by_id()

    # ------------------------------------------------------------------ small pieces

    def id_link(self, item_id: str, *, whole: bool = False) -> str:
        """A bold ID that may break after its hyphens (``whole``: never); a link where it is in this PDF."""
        text = f"\\textbf{{{latex_escape(item_id) if whole else breakable(item_id)}}}"
        if item_id in self.report.unique_ids:
            text = f"\\hyperref[{item_id}]{{{text}}}"
        return f"\\mbox{{{text}}}" if whole else text

    def id_list(self, item_ids: list[str], *, empty: str = "--", separator: str = ", ", whole: bool = False) -> str:
        if not item_ids:
            return empty
        return separator.join(self.id_link(item_id, whole=whole) for item_id in item_ids)

    def volume_text(self, volume_code: str) -> str:
        return latex_escape(short_volume_code(self.report_code, volume_code))

    def evidence_of_shells(self, shell_ids: list[str]) -> list[str]:
        found: list[str] = []
        for shell_id in shell_ids:
            test = self.tests.get(shell_id)
            for evidence_id in test.evidence_ids if test is not None else []:
                if evidence_id not in found:
                    found.append(evidence_id)
        return found

    def _scope_of(self, req_id: str) -> set[str]:
        """``req_id`` and every requirement derived from it, at any depth."""
        if not hasattr(self, "_children"):
            self._children: dict[str, list[str]] = {}
            for requirement in self.requirements.values():
                for parent_id in requirement.parent_ids:
                    self._children.setdefault(parent_id, []).append(requirement.req_id)
        scope: set[str] = set()
        stack = [req_id]
        while stack:
            current = stack.pop()
            if current not in scope:
                scope.add(current)
                stack.extend(self._children.get(current, []))
        return scope

    def record_asserts(self, evidence_id: str, req_id: str) -> bool:
        """Whether the record asserts ``req_id`` (or a requirement derived from it).

        A record whose assertions name no requirement at all counts for every requirement of
        the shells that bind it, as before. Without result folders every bound record counts.
        """
        if not self.report.test_results_dir:
            return True
        named = set(self.report._load_record(evidence_id).requirement_ids)
        return not named or bool(named & self._scope_of(req_id))

    def evidence_of_requirement(self, requirement: Requirement) -> list[str]:
        """The records of the requirement's shells that assert it (see ``record_asserts``)."""
        return [
            evidence_id
            for evidence_id in self.evidence_of_shells(requirement.test_ids)
            if self.record_asserts(evidence_id, requirement.req_id)
        ]

    def tally_cell(self, shell_ids: list[str], evidence_ids: Optional[list[str]] = None) -> str:
        """``passed/total`` for ``evidence_ids`` (default: every record the shells bind)."""
        if evidence_ids is None:
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
        return f"{{\\raggedright\\noindent{self.verified_by_text(requirement)}\\par}}\n"

    def verified_by_text(self, requirement: Requirement) -> str:
        """``Verified by: <shells> (<n> evidence records)``: the records that assert the requirement."""
        if not requirement.test_ids:
            shells = f"\\textcolor{{{_FAILED_COLOUR}}}{{\\textbf{{no test shell}}}}"
        else:
            shells = self.id_list(requirement.test_ids)
        count = len(self.evidence_of_requirement(requirement))
        suffix = f" ({count} evidence record{'s' if count != 1 else ''})" if count else ""
        return f"\\textbf{{Verified by:}} {shells}{suffix}"

    def _volumes_of(self, req_ids: list[str]) -> dict[str, list[str]]:
        return requirement_volumes(
            req_ids,
            plan=self.plan,
            evidence_ids={req_id: self.evidence_of_requirement(self.requirements[req_id]) for req_id in req_ids},
            index=self.report.evidence_index,
        )

    def _volume_cell(self, codes: list[str]) -> str:
        return ", ".join(f"\\mbox{{{self.volume_text(code)}}}" for code in codes) or "--"

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
                        self.tally_cell(requirement.test_ids, self.evidence_of_requirement(requirement)),
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

    # ------------------------------------------------------------------ document set (page 2)

    def document_set_table(self, version: str) -> str:
        if self.document_set is None:
            return ""
        report = latex_escape(f"{self.report_code} {version}".strip())
        rows: list[str] = []
        for entry in self.document_set.entries:
            if entry.tier1_ids:
                scope = "\\newline ".join(self.id_link(tier1_id) for tier1_id in entry.tier1_ids)
            else:
                scope = self.report.process_text(entry.tier1_scope) if entry.tier1_scope else "--"
            code = f"\\textbf{{{breakable(entry.code)}}}"
            if entry.file:
                code += f"\\newline {{\\scriptsize\\texttt{{{breakable(entry.file)}}}}}"
            rows.append(
                self._row(
                    [
                        code,
                        self.report.process_text(entry.title),
                        scope,
                        str(entry.pages),
                        str(entry.records),
                        f"{{\\scriptsize\\texttt{{{breakable(entry.sha256)}}}}}",
                    ]
                )
            )
        return (
            "\\pdfbookmark[1]{Document set}{document-set}\n"
            "\\section*{Document set}\n"
            f"This report, {report}, is issued with the evidence volumes listed below. Approval of the report "
            "covers each of them: a volume is identified by the SHA-256 of its PDF file, and a file whose hash "
            "differs is not the approved file. Each volume holds the full evidence records for the Tier 1 "
            "requirements in its scope, each record exactly once; this report cites them by volume code and "
            "page.\n\n"
            + longtable(
                ["Document", "Title", "Tier 1 scope", "Pages", "Records", "SHA-256"],
                [0.17, 0.29, 0.17, 0.07, 0.09, 0.21],
                rows,
                aligns=["raggedright", "raggedright", "raggedright", "raggedleft", "raggedleft", "raggedright"],
            )
            + "\\clearpage\n"
        )

    # ------------------------------------------------------------------ hierarchy: front matter

    def _section(self, title: str, label: str = "", *, new_page: bool = True) -> str:
        text = self.report.heading_text(title)
        label_text = f"\\label{{{label}}}" if label else ""
        # A new section starts on the same page when a third of a page is left (no near-empty pages).
        return ("\\needspace{0.35\\textheight}\n" if new_page else "") + f"\\section{{{text}}}{label_text}\n"

    def front_matter(self, version: str) -> str:
        assert self.layout is not None
        return self.how_to_read(version) + self.release_results() + self.ius_table()

    def how_to_read(self, version: str) -> str:
        reference = self.report.evidence_mode == "reference"
        report = latex_escape(f"{self.report_code} {version}".strip()) or "this report"
        # With a volume manifest the chapters follow it, volume by volume, which need not be the
        # numeric order of the Tier 1 IDs (E04 may hold T1-005 and T1-010).
        order = "the order of the evidence volumes" if self.plan is not None else "Tier 1 order"
        parts = [
            self._section("How to read this report", "how-to-read", new_page=False),
            "This report is laid out by the requirement hierarchy. Each Tier 1 requirement has a chapter, in "
            f"{order}, holding:\n",
            "\\begin{itemize}",
            "\\item the Tier 1 statement"
            + (", with its source in the Intended Use Statement" if any(c.ius_source for c in self.layout.chapters)
               else "")
            + ", and the risks of the requirements derived from it;",
            "\\item a table of the requirements derived from it: test shells, evidence passed out of the "
            "records that assert the requirement, and risks; a requirement whose records are in another "
            "volume names it;",
            "\\item the Tier 1 journey test shells, which exercise the Tier 1 end to end;",
            "\\item each derived requirement, with one line giving the shells that verify it, the number of "
            "evidence records that assert it and its risks, followed by its test shells: the description, "
            "then the evidence each shell binds. A shell that also verifies other requirements says so "
            "(\\textbf{Also verifies}); a shell printed under another requirement is cited with its page, "
            "with the rows of the records that assert this one.",
            "\\end{itemize}",
        ]
        if reference:
            # The example cites a volume the way the rows do: its full code.
            volumes = sorted({str(entry.get("volume", "")) for entry in self.report.evidence_index.values()} - {""})
            example = f"\\mbox{{{self.volume_text(volumes[0])}}} p.~14" if volumes else "E03 p.~14"
            short_for = (
                f" ({self.volume_text(volumes[0])} is {latex_escape(volumes[0])})"
                if volumes and self.volume_text(volumes[0]) != latex_escape(volumes[0]) else ""
            )
            parts.append(
                "Each bound evidence record is one row: its ID, status, what it asserted for that shell's "
                "requirements, and where the full record is printed, as the short code of its evidence volume "
                f"and a page (for example \\textit{{{example}}}{short_for}). "
                "The volumes print each record once: provenance, description, assertions, output and figures. "
                "A record bound by several shells is a row under each.\n"
            )
        else:
            parts.append("Each bound evidence record is printed in full under the shell that binds it.\n")
        parts.append(
            "Requirement, test and risk IDs are links within this report. After the Tier 1 chapters come the "
            "requirements met by procedure (if any), the risk trace table and two appendices: an index of "
            "every requirement with the page of its statement, followed by the introductions of the "
            "requirement and test files, and an index of every evidence record.\n"
        )
        if self.document_set is not None:
            parts.append(
                f"Approval of {report} covers the documents listed with their SHA-256 in the document-set table "
                "on page 2.\n"
            )
        return "\n".join(parts) + "\n"

    def _bound_records(self) -> list[str]:
        assert self.layout is not None
        found: list[str] = []
        for shell_id in self.layout.shell_ids():
            for evidence_id in self.tests[shell_id].evidence_ids:
                if evidence_id not in found:
                    found.append(evidence_id)
        return found

    def release_results(self) -> str:
        assert self.layout is not None
        evidence_ids = self._bound_records()
        statuses = [self.report.evidence_status(evidence_id) for evidence_id in evidence_ids]
        known = all(statuses)
        passed = sum(1 for status in statuses if status.strip().lower() in _PASSED)
        bindings = sum(len(self.tests[shell_id].evidence_ids) for shell_id in self.layout.shell_ids())
        derived = sum(len(chapter.sections) for chapter in self.layout.chapters)
        rows = [
            ("Tier 1 requirements", str(len(self.layout.chapters))),
            ("Derived requirements", str(derived)),
            ("Procedural requirements", str(len(self.layout.procedural))),
            ("Test shells", str(len(self.layout.shell_ids()))),
            ("Evidence bindings", str(bindings)),
            ("Distinct evidence records", str(len(evidence_ids))),
        ]
        if self.layout.outside:
            rows.insert(3, ("Requirements outside the hierarchy", str(len(self.layout.outside))))
        if known and evidence_ids:
            failed = len(evidence_ids) - passed
            rows.append(("Records passed", f"\\textcolor{{{_PASSED_COLOUR}}}{{\\textbf{{{passed}}}}}"))
            rows.append(
                ("Records not passed",
                 f"\\textcolor{{{_FAILED_COLOUR if failed else 'black'}}}{{\\textbf{{{failed}}}}}")
            )
        parts = [self._section("Release results", "release-results")]
        if self.result_text:
            parts.append(f"\\textbf{{Result:}} {self.report.process_text(self.result_text)}\n")
        parts.append(
            "\\begin{center}\\begin{tabular}{@{}lr@{}}\\toprule\n"
            + "\n".join(f"{label} & {value} \\\\" for label, value in rows)
            + "\n\\bottomrule\\end{tabular}\\end{center}\n"
        )
        if known and evidence_ids and passed < len(evidence_ids):
            parts.append(self.not_passed_table(evidence_ids))
        parts.append(self.volume_counts(evidence_ids))
        parts.append(self.ci_register(evidence_ids))
        return "\n".join(part for part in parts if part)

    def computed_result(self) -> str:
        """The cover's Result, from the bound records: all passed, or how many did not."""
        assert self.layout is not None
        evidence_ids = self._bound_records()
        statuses = [self.report.evidence_status(evidence_id) for evidence_id in evidence_ids]
        if not evidence_ids or not all(statuses):
            return ""
        failed = sum(1 for status in statuses if status.strip().lower() not in _PASSED)
        if failed:
            return f"NOT RELEASABLE: {failed} of {len(evidence_ids)} evidence records did not pass"
        return f"All {len(evidence_ids)} evidence records passed"

    def not_passed_table(self, evidence_ids: list[str]) -> str:
        """Every bound record that did not pass: its status, the shells and requirements, and where."""
        assert self.layout is not None
        shells_of: dict[str, list[str]] = {}
        for shell_id in self.layout.shell_ids():
            for evidence_id in self.tests[shell_id].evidence_ids:
                shells_of.setdefault(evidence_id, []).append(shell_id)
        rows: list[str] = []
        for evidence_id in evidence_ids:
            status = self.report.evidence_status(evidence_id)
            if status.strip().lower() in _PASSED:
                continue
            shells = shells_of.get(evidence_id, [])
            requirement_ids: list[str] = []
            for shell_id in shells:
                for req_id in self.tests[shell_id].req_ids:
                    if req_id not in requirement_ids:
                        requirement_ids.append(req_id)
            rows.append(self._row([
                f"\\textbf{{{breakable(evidence_id)}}}",
                coloured_status(status),
                self.id_list(requirement_ids),
                ", ".join(f"\\pageref{{{shell_id}}}" for shell_id in shells[:3]) or "--",
            ]))
        return (
            "\\subsection{Records not passed}\n"
            + longtable(["Evidence record", "Status", "Requirements", "Report page"], [0.34, 0.14, 0.38, 0.14], rows,
                        aligns=["raggedright", "raggedright", "raggedright", "raggedleft"])
        )

    def volume_counts(self, evidence_ids: list[str]) -> str:
        index = self.report.evidence_index
        if not index:
            return ""
        order: list[str] = []
        if self.plan is not None:
            order = [spec.code for spec in self.plan.manifest.volumes]
        for evidence_id in evidence_ids:
            volume = str(index.get(evidence_id, {}).get("volume", ""))
            if volume and volume not in order:
                order.append(volume)
        rows: list[str] = []
        for code in order:
            ids = [evidence_id for evidence_id in evidence_ids if index.get(evidence_id, {}).get("volume") == code]
            passed = sum(1 for e in ids if str(index[e].get("status", "")).lower() in _PASSED)
            title = ""
            scope: list[str] = []
            if self.plan is not None:
                try:
                    spec = self.plan.manifest.volume(code)
                    title, scope = spec.title, spec.tier1_ids
                except KeyError:
                    pass
            entry = self.document_set.entry(code) if self.document_set is not None else None
            if entry is not None:
                title = title or entry.title
                scope = scope or entry.tier1_ids
            rows.append(
                self._row(
                    [
                        f"\\textbf{{{breakable(code)}}}",
                        self.report.process_text(title) if title else "--",
                        self.id_list(scope, separator="\\newline "),
                        str(len(ids)),
                        str(passed),
                    ]
                )
            )
        return (
            "\\subsection{Evidence records per volume}\n"
            + longtable(
                ["Volume", "Title", "Tier 1 scope", "Records", "Passed"],
                [0.20, 0.38, 0.22, 0.10, 0.10],
                rows,
                aligns=["raggedright", "raggedright", "raggedright", "raggedleft", "raggedleft"],
            )
        )

    def ci_register(self, evidence_ids: list[str]) -> str:
        if not self.report.test_results_dir:
            return ""
        runs = ci_run_register(self.report._load_record(evidence_id) for evidence_id in evidence_ids)
        rows: list[str] = []
        for run in runs:
            if run.in_ci:
                run_text = latex_escape(str(run.run_id)) if run.run_id else "run"
                if run.run_url:
                    run_text = f"\\href{{{self.report._href_url(run.run_url)}}}{{{run_text}}}"
            else:
                run_text = "\\textit{not a CI run}"
            shas: list[str] = []
            for sha in run.head_shas:
                text = f"\\texttt{{{latex_escape(sha[:12])}}}"
                if self.commit and not (sha.lower().startswith(self.commit) or self.commit.startswith(sha.lower())):
                    text = f"\\textcolor{{{_FAILED_COLOUR}}}{{{text}}}"
                shas.append(text)
            records = str(len(run.evidence_ids)) + (f" ({run.failed} not passed)" if run.failed else "")
            rows.append(
                self._row(
                    [
                        self.report.process_text(run.workflow),
                        run_text,
                        "\\newline ".join(shas) or "--",
                        records,
                        coloured_status(run.conclusion),
                    ]
                )
            )
        in_ci = sum(1 for run in runs if run.in_ci)
        note = (
            f"The bound evidence records come from {in_ci} CI workflow run{'s' if in_ci != 1 else ''}"
            + (
                f" and from {len(runs) - in_ci} group{'s' if len(runs) - in_ci != 1 else ''} of records "
                "with no CI run"
                if len(runs) > in_ci else ""
            )
            + ". Each run is listed once; the records themselves do not repeat its link. The conclusion is the "
            "workflow's own where the run has a workflow record, else whether every record from it passed."
        )
        if self.commit:
            note += f" A head commit other than {latex_escape(self.commit[:12])} is shown in red."
        return (
            "\\subsection{CI run register}\n" + note + "\n\n"
            + longtable(
                ["Workflow", "Run", "Head commit", "Records", "Conclusion"],
                [0.30, 0.20, 0.20, 0.15, 0.15],
                rows,
            )
        )

    def ius_table(self) -> str:
        assert self.layout is not None
        chapters = [chapter for chapter in self.layout.chapters if chapter.ius_source]
        if not chapters:
            return ""
        rows = [
            self._row(
                [
                    self.id_link(chapter.tier1.req_id),
                    self.report.process_text(chapter.tier1.title),
                    self.report.process_text(chapter.ius_source),
                ]
            )
            for chapter in chapters
        ]
        return self._section("Intended use to Tier 1", "ius-to-tier1") + longtable(
            ["Tier 1", "Title", "IUS source"], [0.22, 0.38, 0.40], rows
        )

    # ------------------------------------------------------------------ hierarchy: chapters

    def _shell(self, test: Test, command: str, *, under: str = "") -> str:
        return self.report._render_shell(test, command=command, under=under)

    def _requirement_lines(self, requirement: Requirement, *, chapter_id: str = "",
                           rolled_up: Optional[list[str]] = None) -> str:
        """One line: ``Verified by: ... · Risks: ...``.

        ``Parent`` is printed only when it is not the chapter the requirement sits in.
        ``rolled_up``: a Tier 1's risks, gathered from the requirements derived from it.
        """
        pieces: list[str] = []
        parents = [parent_id for parent_id in requirement.parent_ids if parent_id != chapter_id]
        if parents or (requirement.parent_ids and not chapter_id):
            pieces.append(f"\\textbf{{Parent:}} {self.id_list(requirement.parent_ids)}")
        pieces.append(self.verified_by_text(requirement))
        if self.document.risks:
            risk_ids = list(requirement.risk_ids)
            label = "Risks"
            if rolled_up is not None:
                for risk_id in rolled_up:
                    if risk_id not in risk_ids:
                        risk_ids.append(risk_id)
                label = "Risks (of its derived requirements)" if rolled_up and not requirement.risk_ids else "Risks"
            pieces.append(f"\\textbf{{{label}:}} {self.id_list(risk_ids, empty='none recorded')}")
        return "{\\raggedright\\small\\noindent " + "\\enspace\\textperiodcentered\\enspace ".join(pieces) + "\\par}\n"

    def _printed_under(self) -> dict[str, str]:
        """Shell ID -> the requirement (or Tier 1, for a journey shell) it is printed under."""
        if not hasattr(self, "_under"):
            self._under: dict[str, str] = {}
            if self.layout is not None:
                for chapter in self.layout.chapters:
                    for test in chapter.journey_shells:
                        self._under.setdefault(test.test_id, chapter.tier1.req_id)
                for section in self.layout.all_sections():
                    for test in section.shells:
                        self._under.setdefault(test.test_id, section.requirement.req_id)
        return self._under

    def _shell_elsewhere(self, requirement: Requirement, shell_id: str) -> str:
        """A shell of ``requirement`` printed under another: its page and the rows that assert this one."""
        under = self._printed_under().get(shell_id)
        test = self.tests.get(shell_id)
        if under is None or test is None:
            return ""
        where = (
            f"printed under {self.id_link(under)}, p.~\\pageref{{{shell_id}}}" if under != shell_id
            else f"p.~\\pageref{{{shell_id}}}"
        )
        evidence_ids = [e for e in test.evidence_ids if self.record_asserts(e, requirement.req_id)]
        text = (
            "\\par\\needspace{4\\baselineskip}\\smallskip{\\raggedright\\small\\noindent"
            f"\\textbf{{{latex_escape(shell_id)}}}: {self.report.process_text(test.title)} ({where})"
        )
        if not evidence_ids:
            return text + ".\\par}\n"
        text += f". The records that assert {latex_escape(requirement.req_id)}:\\par}}\n"
        return text + self.report.render_reference_rows(evidence_ids, scope={requirement.req_id})

    def requirement_section(self, section: RequirementSection, chapter_id: str = "") -> str:
        requirement = section.requirement
        heading = self.report.heading_text(f"{requirement.req_id}: {requirement.title}")
        parts = [
            # Keep the heading with its line and the start of its statement.
            f"\\needspace{{5\\baselineskip}}\\subsection{{{heading}}}\\label{{{requirement.req_id}}}\n",
            self._requirement_lines(requirement, chapter_id=chapter_id),
            self.report.md_to_latex(requirement.body_content()),
            "\n",
        ]
        printed = {test.test_id for test in section.shells}
        for test in section.shells:
            parts.append(self._shell(test, "subsubsection", under=requirement.req_id))
        if self.report.evidence_mode == "reference":
            for shell_id in requirement.test_ids:
                if shell_id not in printed:
                    parts.append(self._shell_elsewhere(requirement, shell_id))
        return "".join(parts)

    def rollup_table(self, chapter: Tier1Chapter) -> str:
        assert self.layout is not None
        volumes = self._volumes_of([requirement.req_id for requirement in chapter.derived])
        home = self.chapter_volume(chapter)
        rows: list[str] = []
        for requirement in chapter.derived:
            title = self.report.process_text(requirement.title)
            if self.layout.tier1_of.get(requirement.req_id, [chapter.tier1.req_id])[0] != chapter.tier1.req_id:
                title += " \\textit{(printed under " + self.id_link(self.layout.tier1_of[requirement.req_id][0]) + ")}"
            elsewhere = [code for code in volumes.get(requirement.req_id, []) if code != home]
            if elsewhere:
                title += " \\textit{(records in " + self._volume_cell(volumes[requirement.req_id]) + ")}"
            rows.append(
                self._row(
                    [
                        self.id_link(requirement.req_id),
                        title,
                        self.id_list(requirement.test_ids),
                        self.tally_cell(requirement.test_ids, self.evidence_of_requirement(requirement)),
                        self.id_list(requirement.risk_ids),
                    ],
                    highlight=not requirement.test_ids,
                )
            )
        if not rows:
            return "No requirement is derived from this Tier 1.\n\n"
        return row_tables(
            ["Requirement", "Title", "Shells", "Evidence", "Risks"],
            [0.27, 0.32, 0.22, 0.08, 0.11],
            rows,
        )

    def chapter_volume(self, chapter: Tier1Chapter) -> str:
        """The evidence volume of a Tier 1 chapter (from the manifest), or ``""``."""
        if self.plan is None:
            return ""
        for spec in self.plan.manifest.volumes:
            if chapter.tier1.req_id in spec.tier1_ids:
                return spec.code
        return ""

    def _chapter_summary(self, chapter: Tier1Chapter) -> str:
        shells: list[str] = []
        for requirement in [chapter.tier1] + chapter.derived:
            for shell_id in requirement.test_ids:
                if shell_id not in shells:
                    shells.append(shell_id)
        evidence_ids = self.evidence_of_shells(shells)
        statuses = [self.report.evidence_status(evidence_id) for evidence_id in evidence_ids]
        text = (
            f"{len(chapter.derived)} derived requirement{'s' if len(chapter.derived) != 1 else ''}, "
            f"{len(shells)} test shell{'s' if len(shells) != 1 else ''} and "
            f"{len(evidence_ids)} evidence record{'s' if len(evidence_ids) != 1 else ''}"
        )
        if evidence_ids and all(statuses):
            passed = sum(1 for status in statuses if status.strip().lower() in _PASSED)
            text += f", of which {passed} passed"
        uncovered = [requirement.req_id for requirement in chapter.derived if not requirement.test_ids]
        if uncovered:
            text += (
                f". \\textcolor{{{_FAILED_COLOUR}}}{{\\textbf{{{len(uncovered)} derived requirement"
                f"{'s' if len(uncovered) != 1 else ''} with no test shell}}}} (red rows)"
            )
        return text + ".\n\n"

    def tier1_chapter(self, chapter: Tier1Chapter) -> str:
        tier1 = chapter.tier1
        heading = self.report.heading_text(f"{tier1.req_id}: {tier1.title}")
        parts = [
            f"\\needspace{{0.35\\textheight}}\n\\section{{{heading}}}\\label{{{tier1.req_id}}}\n",
            self.report.md_to_latex(chapter.statement),
            "\n\n",
        ]
        if chapter.ius_source:
            parts.append(
                f"{{\\raggedright\\small\\noindent\\textbf{{IUS Source:}} "
                f"{self.report.process_text(chapter.ius_source)}\\par}}\n"
            )
        rolled: list[str] = []
        for requirement in chapter.derived:
            for risk_id in requirement.risk_ids:
                if risk_id not in rolled:
                    rolled.append(risk_id)
        parts.append(self._requirement_lines(tier1, rolled_up=rolled))
        parts.append("\\subsection*{Derived requirements}\\phantomsection"
                     "\\addcontentsline{toc}{subsection}{Derived requirements}\n")
        parts.append(self._chapter_summary(chapter))
        home = self.chapter_volume(chapter)
        if home and self.report.evidence_mode == "reference":
            parts.append(
                f"The full evidence records of this chapter are in \\mbox{{{self.volume_text(home)}}} unless "
                "a row names another volume.\n\n"
            )
        parts.append(self.rollup_table(chapter))
        if chapter.journey_shells:
            parts.append("\n\\subsection*{Tier 1 journey}\\phantomsection"
                         "\\addcontentsline{toc}{subsection}{Tier 1 journey}\n")
            for test in chapter.journey_shells:
                parts.append(self._shell(test, "subsubsection", under=tier1.req_id))
        for section in chapter.sections:
            parts.append(self.requirement_section(section, chapter_id=tier1.req_id))
        return "".join(parts)

    def chapters(self) -> str:
        assert self.layout is not None
        return "".join(self.tier1_chapter(chapter) for chapter in self.layout.chapters)

    def procedural_chapter(self) -> str:
        assert self.layout is not None
        parts: list[str] = []
        if self.layout.procedural:
            parts.append(self._section("Procedural Controls", "procedural-controls"))
            parts.append(
                "These requirements have no Tier 1 parent: they are met by procedure rather than by software "
                "behaviour alone. Each lists what verifies it.\n\n"
            )
            parts += [self.requirement_section(section) for section in self.layout.procedural]
        if self.layout.outside or self.layout.loose_shells:
            parts.append(self._section("Requirements outside the Tier 1 hierarchy", "outside-hierarchy"))
            parts.append(
                "These requirements do not trace to a Tier 1 requirement, and these test shells verify no "
                "requirement. They are printed here so that nothing in the corpus is left out.\n\n"
            )
            parts += [self.requirement_section(section) for section in self.layout.outside]
            for test in self.layout.loose_shells:
                parts.append(self.report._render_shell(test, command="subsection"))
        return "".join(parts)

    # ------------------------------------------------------------------ risk trace table

    def risk_trace(self) -> str:
        risks = [risk for doc in self.document.risks for risk in doc.items]
        if not risks:
            return ""
        rows: list[str] = []
        for risk in risks:
            shells = [shell_id for shell_id in risk.test_refs if shell_id in self.tests]
            requirement_ids: list[str] = [req_id for req_id in risk.requirement_refs if req_id in self.requirements]
            for shell_id in shells:
                for req_id in self.tests[shell_id].req_ids:
                    if req_id not in requirement_ids and req_id in self.requirements:
                        requirement_ids.append(req_id)
            volumes: list[str] = []
            if self.plan is not None:
                for shell_id in shells:
                    code = self.plan.shells[shell_id].volume_code if shell_id in self.plan.shells else None
                    if code and code not in volumes:
                        volumes.append(code)
                # In manifest order ("E03, E04"), not the order the register happens to cite shells.
                position = {spec.code: spec.index for spec in self.plan.manifest.volumes}
                volumes.sort(key=lambda code: (position.get(code, len(position) + 1), code))
            else:
                for evidence_id in self.evidence_of_shells(shells):
                    code = str(self.report.evidence_index.get(evidence_id, {}).get("volume", "") or "")
                    if code and code not in volumes:
                        volumes.append(code)
                volumes.sort()
            controls = self.control_summary(risk.attributes.get("controls", ""), risk.risk_id)
            # \leavevmode: a label at the very start of a p-cell would push the text down a line.
            first = (
                f"\\leavevmode\\phantomsection\\label{{{risk.risk_id}}}\\textbf{{{breakable(risk.risk_id)}}}: "
                + self.report.process_text(risk.title)
            )
            rows.append(
                self._row(
                    [
                        first,
                        controls or "--",
                        self.id_list(requirement_ids, separator="\\newline ", whole=True),
                        self.id_list(shells, separator="\\newline ", whole=True),
                        self.tally_cell(shells) if shells else "--",
                        self._volume_cell(volumes),
                    ],
                    highlight=not shells,
                )
            )
        where = (
            f" The full register, with hazards, harms, causes and the risk scores before and after control, is "
            f"{self.report.process_text(self.risk_register_ref)}."
            if self.risk_register_ref
            else " The full register, with hazards, harms, causes and risk scores, is issued separately."
        )
        return (
            self._section("Risk trace", "risk-trace")
            + "Each risk, its controls, the requirements and test shells that verify those controls, the status "
            "of the evidence those shells bind and the volumes that hold it. A risk with no test shell is a red "
            "row." + where + "\n\n"
            + row_tables(
                ["Risk", "Controls (summary)", "Requirements", "Shells", "Evidence", "Vol."],
                [0.15, 0.20, 0.25, 0.26, 0.08, 0.06],
                rows,
                size="scriptsize",
            )
        )

    def control_summary(self, controls: str, risk_id: str, *, limit: int = 200) -> str:
        """The first sentences or clauses of a risk's controls, up to about ``limit`` characters,
        and where the full text is."""
        text = " ".join(controls.split())
        if not text:
            return ""
        parts = re.split(r"(?<=[.;])\s+", text)
        kept = [parts[0]]
        for part in parts[1:]:
            if len(" ".join(kept + [part])) > limit:
                break
            kept.append(part)
        summary = " ".join(kept)
        latex = self.report.process_text(summary)
        if len(kept) < len(parts):
            where = self.report.process_text(self.risk_register_ref.split(",")[0]) if self.risk_register_ref else (
                "the risk register")
            latex += f" \\textit{{(full text: {where}, {latex_escape(risk_id)})}}"
        return latex

    # ------------------------------------------------------------------ appendices

    @staticmethod
    def _demoted(items: list[dict]) -> list[dict]:
        """File prose under an appendix sub-heading: headings unnumbered, at level 3."""
        demoted = copy.deepcopy(items)
        for item in demoted:
            if item.get("type") == "heading":
                item["level"] = 3
                item["_unnumbered"] = True
        return demoted

    @staticmethod
    def _two_column_index(entries: list[str]) -> str:
        """Index lines set in two balanced columns; each entry is one paragraph."""
        if not entries:
            return ""
        return (
            "{\\footnotesize\\setlength{\\columnsep}{1.2em}\\setlength{\\parskip}{0pt}"
            "\\begin{multicols}{2}\\raggedright\n"
            + "\n".join(entries)
            + "\n\\end{multicols}\\par}\n"
        )

    @staticmethod
    def _index_entry(left: str, right: str) -> str:
        return f"\\noindent\\hangindent=1.5em {left}\\nobreak\\hfill\\nobreak {right}\\par"

    def area_appendix(self) -> str:
        assert self.layout is not None
        requirements = sorted(
            (requirement for doc in self.document.requirements for requirement in doc.items),
            key=lambda requirement: (requirement.req_id.casefold(), requirement.req_id),
        )
        entries = [
            self._index_entry(self.id_link(requirement.req_id, whole=True),
                              f"p.~\\pageref{{{requirement.req_id}}}")
            for requirement in requirements
        ]
        parts = [
            self._section("Appendix A: Requirement index", "appendix-areas"),
            "Every requirement of the corpus, in ID order, with the page of its statement. The Tier 1 chapter "
            "of each is the chapter that page is in. The introductions of the requirement and test files "
            "follow the index.\n",
            self._two_column_index(entries),
        ]
        with_prose = [doc for doc in self.document.requirements if doc.generic_content]
        if with_prose:
            parts.append("\\subsection{Requirement files}\n")
            for doc in with_prose:
                parts.append(f"\\subsubsection*{{{self.report.heading_text(doc.title)}}}\n")
                if doc.source_path:
                    parts.append(f"{{\\footnotesize Source file: \\texttt{{{breakable(doc.source_path)}}}\\par}}\n")
                parts.append(self.report.md_to_latex(self._demoted(doc.generic_content)) + "\n\n")
        tests_with_prose = [doc for doc in self.document.tests if doc.generic_content]
        if tests_with_prose:
            parts.append("\\subsection{Test documents}\n")
            parts.append("The introductions of the test files, which describe how each area is tested.\n\n")
            for doc in tests_with_prose:
                parts.append(f"\\subsubsection*{{{self.report.heading_text(doc.title)}}}\n")
                if doc.source_path:
                    parts.append(f"{{\\footnotesize Source file: \\texttt{{{breakable(doc.source_path)}}}\\par}}\n")
                parts.append(self.report.md_to_latex(self._demoted(doc.generic_content)) + "\n\n")
                if doc.items:
                    parts.append(
                        "{\\raggedright\\small\\textbf{Shells:} "
                        + self.id_list([item.test_id for item in doc.items])
                        + "\\par}\n\n"
                    )
        return "".join(parts)

    def evidence_index_appendix(self) -> str:
        assert self.layout is not None
        first_shell: dict[str, str] = {}
        for shell_id in self.layout.shell_ids():
            for evidence_id in self.tests[shell_id].evidence_ids:
                first_shell.setdefault(evidence_id, shell_id)
        if not first_shell:
            return ""
        reference = self.report.evidence_mode == "reference"
        entries: list[str] = []
        for evidence_id in sorted(first_shell, key=lambda value: (value.casefold(), value)):
            status = self.report.evidence_status(evidence_id)
            if reference:
                entry = self.report.evidence_index.get(evidence_id, {})
                volume = self.volume_text(str(entry.get("volume", "")))
                where = f"{volume}~p.~{latex_escape(str(entry.get('page', '')))}"
            else:
                where = f"p.~\\pageref{{{evidence_anchor(evidence_id)}}}"
            passed = status.strip().lower() in _PASSED
            mark = (
                f"\\textcolor{{{_PASSED_COLOUR}}}{{Pass}}" if passed
                else (coloured_status(status) if status else "--")
            )
            entries.append(
                self._index_entry(
                    f"\\hyperref[{first_shell[evidence_id]}]{{{breakable(evidence_id)}}}",
                    f"{mark}\\enspace\\mbox{{{where}}}",
                )
            )
        return (
            self._section("Appendix B: Evidence index", "appendix-evidence-index")
            + "Every evidence record bound in this report, by ID, with its status (\\textit{Pass} for passed) and "
            + ("the volume and page of the full record. " if reference else "the page of the full record. ")
            + "Each ID links to the first test shell in this report that binds it.\n\n"
            + self._two_column_index(entries)
        )
