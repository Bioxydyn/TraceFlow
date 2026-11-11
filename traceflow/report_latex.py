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
        return ("\\clearpage\n" if new_page else "") + f"\\section{{{text}}}{label_text}\n"

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
            "\\item the Tier 1 statement, with its source in the Intended Use Statement where one is given;",
            "\\item a table of the requirements derived from it: area, test shells, evidence passed out of "
            "bound, risks and evidence volume;",
            "\\item the Tier 1 journey test shells, which exercise the Tier 1 end to end;",
            "\\item each derived requirement, with its \\textbf{Parent}, \\textbf{Verified by} and "
            "\\textbf{Risks} lines, followed by the test shells that verify it: description, steps and "
            "expected result, then the evidence each shell binds.",
            "\\end{itemize}",
        ]
        if reference:
            # The example cites a volume the way the rows do: its full code.
            volumes = sorted({str(entry.get("volume", "")) for entry in self.report.evidence_index.values()} - {""})
            example = f"\\mbox{{{latex_escape(volumes[0])}}} p.~14" if volumes else "E03 p.~14"
            parts.append(
                "Each bound evidence record is one row: its ID, status, kind, what it asserted for that shell's "
                "requirements, and where the full record is printed, as an evidence-volume code and page "
                f"(for example \\textit{{{example}}}). The volumes print each record once: provenance, "
                "description, assertions, output and figures. A record bound by several shells is a row under "
                "each.\n"
            )
        else:
            parts.append("Each bound evidence record is printed in full under the shell that binds it.\n")
        parts.append(
            "Requirement, test and risk IDs are links within this report. After the Tier 1 chapters come the "
            "requirements met by procedure (if any), the risk trace table and two appendices: the requirements "
            "by area (source file) and an index of every evidence record.\n"
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
        parts.append(self.volume_counts(evidence_ids))
        parts.append(self.ci_register(evidence_ids))
        return "\n".join(part for part in parts if part)

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

    def _shell(self, test: Test, command: str) -> str:
        return self.report._render_shell(test, command=command)

    def _requirement_lines(self, requirement: Requirement, *, parent: bool) -> str:
        lines = ["{\\raggedright\\small"]
        if parent:
            lines.append(f"\\noindent\\textbf{{Parent:}} {self.id_list(requirement.parent_ids, empty='none')}\\par")
        lines.append(self.verified_by_line(requirement, always=True).strip())
        if self.document.risks:
            risks = self.id_list(requirement.risk_ids, empty="none recorded")
            lines.append(f"\\noindent\\textbf{{Risks:}} {risks}\\par")
        lines.append("}")
        return "\n".join(lines) + "\n"

    def requirement_section(self, section: RequirementSection) -> str:
        requirement = section.requirement
        heading = self.report.heading_text(f"{requirement.req_id}: {requirement.title}")
        parts = [
            # Keep the heading with its Area/Parent/Verified by/Risks lines.
            f"\\needspace{{4\\baselineskip}}\\subsection{{{heading}}}\\label{{{requirement.req_id}}}\n",
            f"{{\\footnotesize\\color{{black!70}}Area: {self.report.process_text(section.area)}\\par}}\n",
            self._requirement_lines(requirement, parent=True),
            self.report.md_to_latex(requirement.body_content()),
            "\n",
        ]
        for test in section.shells:
            parts.append(self._shell(test, "subsubsection"))
        return "".join(parts)

    def rollup_table(self, chapter: Tier1Chapter) -> str:
        assert self.layout is not None
        volumes = self._volumes_of([requirement.req_id for requirement in chapter.derived])
        rows: list[str] = []
        for requirement in chapter.derived:
            title = self.report.process_text(requirement.title)
            if self.layout.tier1_of.get(requirement.req_id, [chapter.tier1.req_id])[0] != chapter.tier1.req_id:
                title += " \\textit{(printed under " + self.id_link(self.layout.tier1_of[requirement.req_id][0]) + ")}"
            rows.append(
                self._row(
                    [
                        self.id_link(requirement.req_id),
                        title,
                        self.report.process_text(self.layout.areas.get(requirement.req_id, "")),
                        self.id_list(requirement.test_ids),
                        self.tally_cell(requirement.test_ids),
                        self.id_list(requirement.risk_ids),
                        self._volume_cell(volumes.get(requirement.req_id, [])),
                    ],
                    highlight=not requirement.test_ids,
                )
            )
        if not rows:
            return "No requirement is derived from this Tier 1.\n\n"
        return longtable(
            ["Requirement", "Title", "Area", "Shells", "Evidence", "Risks", "Vol."],
            [0.22, 0.18, 0.10, 0.25, 0.08, 0.11, 0.06],
            rows,
            size="scriptsize",
        )

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
            f"\\clearpage\n\\section{{{heading}}}\\label{{{tier1.req_id}}}\n",
            self.report.md_to_latex(chapter.statement),
            "\n\n",
        ]
        if chapter.ius_source:
            parts.append(
                f"{{\\raggedright\\small\\noindent\\textbf{{IUS Source:}} "
                f"{self.report.process_text(chapter.ius_source)}\\par}}\n"
            )
        parts.append(self._requirement_lines(tier1, parent=False))
        parts.append("\\subsection*{Derived requirements}\\phantomsection"
                     "\\addcontentsline{toc}{subsection}{Derived requirements}\n")
        parts.append(self._chapter_summary(chapter))
        parts.append(self.rollup_table(chapter))
        if chapter.journey_shells:
            parts.append("\n\\subsection*{Tier 1 journey}\\phantomsection"
                         "\\addcontentsline{toc}{subsection}{Tier 1 journey}\n")
            for test in chapter.journey_shells:
                parts.append(self._shell(test, "subsubsection"))
        for section in chapter.sections:
            parts.append(self.requirement_section(section))
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
            controls = risk.attributes.get("controls", "")
            # \leavevmode: a label at the very start of a p-cell would push the text down a line.
            first = (
                f"\\leavevmode\\phantomsection\\label{{{risk.risk_id}}}\\textbf{{{breakable(risk.risk_id)}}}: "
                + self.report.process_text(risk.title)
            )
            rows.append(
                self._row(
                    [
                        first,
                        self.report.process_text(controls) if controls else "--",
                        self.id_list(requirement_ids, separator="\\newline "),
                        self.id_list(shells, separator="\\newline "),
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
            + longtable(
                ["Risk", "Controls", "Requirements", "Shells", "Evidence", "Vol."],
                [0.16, 0.36, 0.17, 0.17, 0.08, 0.06],
                rows,
                size="scriptsize",
            )
        )

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

    def area_appendix(self) -> str:
        assert self.layout is not None
        parts = [
            self._section("Appendix A: Requirements by area", "appendix-areas"),
            "Every requirement file (area) of the corpus, with its introduction and its requirements, each "
            "with the Tier 1 it traces to and the page of its statement. Clause-led readers (21 CFR Part 11, "
            "EU GMP Annex 11) can start here.\n",
        ]
        for doc in self.document.requirements:
            parts.append(f"\\subsection{{{self.report.heading_text(doc.title)}}}\n")
            if doc.source_path:
                parts.append(f"{{\\footnotesize Source file: \\texttt{{{breakable(doc.source_path)}}}\\par}}\n")
            if doc.generic_content:
                parts.append(self.report.md_to_latex(self._demoted(doc.generic_content)) + "\n\n")
            rows = [
                self._row(
                    [
                        self.id_link(requirement.req_id),
                        self.report.process_text(requirement.title),
                        self.id_list(self.layout.tier1_of.get(requirement.req_id, []), empty="--"),
                        f"\\pageref{{{requirement.req_id}}}",
                    ]
                )
                for requirement in doc.items
            ]
            if rows:
                parts.append(
                    longtable(
                        ["Requirement", "Title", "Tier 1", "Page"],
                        [0.30, 0.47, 0.15, 0.08],
                        rows,
                        aligns=["raggedright", "raggedright", "raggedright", "raggedleft"],
                    )
                )
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
        rows: list[str] = []
        for evidence_id in sorted(first_shell, key=lambda value: (value.casefold(), value)):
            status = self.report.evidence_status(evidence_id)
            if reference:
                entry = self.report.evidence_index.get(evidence_id, {})
                volume = self.volume_text(str(entry.get("volume", "")))
                page = latex_escape(str(entry.get("page", "")))
            else:
                volume = "this report"
                page = f"\\pageref{{{evidence_anchor(evidence_id)}}}"
            rows.append(
                self._row(
                    [
                        f"\\hyperref[{first_shell[evidence_id]}]{{{breakable(evidence_id)}}}",
                        coloured_status(status) if status else "--",
                        volume,
                        page,
                    ]
                )
            )
        return (
            self._section("Appendix B: Evidence index", "appendix-evidence-index")
            + "Every evidence record bound in this report, by ID, with its status and the volume and page of the "
            "full record. Each ID links to the first test shell in this report that binds it.\n\n"
            + longtable(
                ["Evidence record", "Status", "Volume", "Page"],
                [0.66, 0.14, 0.12, 0.08],
                rows,
                aligns=["raggedright", "raggedright", "raggedright", "raggedleft"],
            )
        )
