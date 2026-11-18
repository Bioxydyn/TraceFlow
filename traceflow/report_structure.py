"""The signed report's structure: what goes where, independent of LaTeX.

``--structure hierarchy`` lays the report out by the requirement hierarchy instead of by
source file: one chapter per Tier 1 holding its statement, its derived-requirements table,
its journey shells and then each derived (Tier 2) requirement with the test shells that
verify it. Requirements in procedural files, and any outside the hierarchy, get chapters
of their own, so every requirement and every shell is printed exactly once.

This module computes that layout, reads the document-set JSON, groups evidence records into
the CI run register, and produces the generation date (honouring ``SOURCE_DATE_EPOCH``).
``traceflow.pdf_generator.PdfReport`` turns the results into LaTeX.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Iterable, Optional, cast

from traceflow.parser import Document, HierarchyConfig, Requirement, Test, math_source

if TYPE_CHECKING:  # pragma: no cover - typing only
    from traceflow.evidence import EvidenceRecord
    from traceflow.volumes import VolumePlan

STRUCTURES = ("files", "hierarchy")
MATRIX_STYLES = ("tick", "list")
DEFAULT_DATE_FORMAT = "%d %B %Y"

_MONTHS = (
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
)
_HEX = re.compile(r"[0-9a-fA-F]+")
_SHA256 = re.compile(r"[0-9a-f]{64}")
# ``IUS Source: ...``, ``**Source:** ...`` or ``IUS basis: ...`` in a Tier 1 requirement.
_IUS_SOURCE_LINE = re.compile(
    r"^\s*(?:\*\*|__)?\s*(?:IUS(?:\s+(?:source|basis))?|source)\s*(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*(.+?)\s*$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------- dates


def source_date_epoch() -> Optional[int]:
    """``SOURCE_DATE_EPOCH`` as an integer, or ``None`` when it is not set."""
    raw = os.environ.get("SOURCE_DATE_EPOCH", "").strip()
    if not raw:
        return None
    if not raw.isdigit():
        raise ValueError(f"SOURCE_DATE_EPOCH must be a whole number of seconds since 1970, not `{raw}`")
    return int(raw)


def build_moment() -> datetime:
    """The build time: ``SOURCE_DATE_EPOCH`` (UTC) when set, else now (local time)."""
    epoch = source_date_epoch()
    if epoch is not None:
        return datetime.fromtimestamp(epoch, tz=timezone.utc)
    return datetime.now()


def format_date(moment: datetime, date_format: str) -> str:
    """``strftime`` with English month names whatever the locale (``%B``, ``%b``)."""
    month = _MONTHS[moment.month - 1]
    pieces = date_format.split("%%")
    pieces = [piece.replace("%B", month).replace("%b", month[:3]) for piece in pieces]
    return "%".join(moment.strftime(piece) for piece in pieces)


def generated_date(date_format: str = DEFAULT_DATE_FORMAT) -> str:
    return format_date(build_moment(), date_format)


def check_commit(commit: Optional[str]) -> str:
    """A commit SHA (7-40 hex characters), lower-cased; ``""`` for none."""
    if commit is None or not commit.strip():
        return ""
    value = commit.strip()
    if not _HEX.fullmatch(value) or not 7 <= len(value) <= 40:
        raise ValueError(f"--commit must be a git commit SHA (7 to 40 hex characters), not `{value}`")
    return value.lower()


# ---------------------------------------------------------------------- document set


class DocumentSetError(ValueError):
    """The document-set JSON is malformed or disagrees with the report's inputs."""


@dataclass
class DocumentSetEntry:
    """One file of the set, as the report's page-2 table lists it."""

    code: str
    title: str
    sha256: str
    pages: int
    records: int
    file: str = ""
    tier1_ids: list[str] = field(default_factory=list)
    tier1_scope: str = ""
    commit: str = ""
    size_bytes: Optional[int] = None


@dataclass
class DocumentSet:
    path: str
    entries: list[DocumentSetEntry]
    commit: str = ""
    version: str = ""

    def entry(self, code: str) -> Optional[DocumentSetEntry]:
        for entry in self.entries:
            if entry.code == code:
                return entry
        return None


_SET_KEYS = {"documents", "report", "version", "commit", "schema"}
_ENTRY_KEYS = {"code", "title", "sha256", "pages", "records", "file", "tier1", "tier1_scope", "commit", "size_bytes"}


def _whole_number(value: object, what: str, errors: list[str], *, minimum: int) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        errors.append(f"{what} must be a whole number of at least {minimum}")
        return None
    return value


def parse_document_set(raw: object, path: str = "<document set>") -> DocumentSet:
    """Validate a loaded document-set JSON. Every problem is reported at once.

    Either a list of entries or ``{"documents": [...], "commit": ..., "version": ...}``. Each
    entry: ``code``, ``title``, ``sha256`` (64 lower-case hex), ``pages``, ``records``, and
    optionally ``file``, ``tier1`` (list of IDs) or ``tier1_scope`` (text), ``commit`` and
    ``size_bytes``. Unknown keys are refused.
    """
    errors: list[str] = []
    commit = version = ""
    if isinstance(raw, dict):
        data = cast("dict[str, object]", raw)
        unknown = sorted(set(data) - _SET_KEYS)
        if unknown:
            errors.append(f"unknown top-level keys {unknown} (known: {sorted(_SET_KEYS)})")
        documents = data.get("documents")
        commit = str(data.get("commit") or "").strip()
        version = str(data.get("version") or "").strip()
    else:
        documents = raw
    if not isinstance(documents, list) or not documents:
        raise DocumentSetError(f"{path}: needs a non-empty list of documents" + (f"; {errors[0]}" if errors else ""))
    entries: list[DocumentSetEntry] = []
    seen: set[str] = set()
    for position, item in enumerate(cast("list[object]", documents)):
        where = f"documents[{position}]"
        if not isinstance(item, dict):
            errors.append(f"{where} must be an object")
            continue
        entry = cast("dict[str, object]", item)
        unknown = sorted(set(entry) - _ENTRY_KEYS)
        if unknown:
            errors.append(f"{where} has unknown keys {unknown} (known: {sorted(_ENTRY_KEYS)})")
        code = entry.get("code")
        title = entry.get("title")
        if not isinstance(code, str) or not code.strip():
            errors.append(f"{where}.code must be a non-empty string")
            code = ""
        where = f"document `{code}`" if code else where
        if code in seen:
            errors.append(f"{where} is listed twice")
        seen.add(str(code))
        if not isinstance(title, str) or not title.strip():
            errors.append(f"{where} title must be a non-empty string")
            title = ""
        sha256 = entry.get("sha256")
        if not isinstance(sha256, str) or not _SHA256.fullmatch(sha256):
            errors.append(f"{where} sha256 must be 64 lower-case hex characters")
            sha256 = ""
        pages = _whole_number(entry.get("pages"), f"{where} pages", errors, minimum=1)
        records = _whole_number(entry.get("records"), f"{where} records", errors, minimum=0)
        tier1 = entry.get("tier1", [])
        tier1_items = cast("list[object]", tier1) if isinstance(tier1, list) else None
        if tier1_items is None or not all(isinstance(t, str) and t.strip() for t in tier1_items):
            errors.append(f"{where} tier1 must be a list of requirement IDs")
            tier1 = []
        scope = entry.get("tier1_scope", "")
        if not isinstance(scope, str):
            errors.append(f"{where} tier1_scope must be a string")
            scope = ""
        file_name = entry.get("file", "")
        if not isinstance(file_name, str):
            errors.append(f"{where} file must be a string")
            file_name = ""
        entry_commit = entry.get("commit", "")
        if not isinstance(entry_commit, str) or (entry_commit and not _HEX.fullmatch(entry_commit)):
            errors.append(f"{where} commit must be a hex commit SHA")
            entry_commit = ""
        size = entry.get("size_bytes")
        size_bytes = None if size is None else _whole_number(size, f"{where} size_bytes", errors, minimum=1)
        entries.append(
            DocumentSetEntry(
                code=str(code).strip(),
                title=str(title).strip(),
                sha256=str(sha256),
                pages=pages or 0,
                records=records or 0,
                file=file_name.strip(),
                tier1_ids=[str(t).strip() for t in cast("list[object]", tier1)],
                tier1_scope=scope.strip(),
                commit=entry_commit.lower(),
                size_bytes=size_bytes,
            )
        )
    if errors:
        raise DocumentSetError(f"Document set {path} is invalid:\n  - " + "\n  - ".join(errors))
    return DocumentSet(path=path, entries=entries, commit=commit.lower(), version=version)


def load_document_set(path: str) -> DocumentSet:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except FileNotFoundError as exc:
        raise DocumentSetError(f"Document set {path} does not exist") from exc
    except json.JSONDecodeError as exc:
        raise DocumentSetError(f"Document set {path} is not valid JSON: {exc}") from exc
    return parse_document_set(raw, path)


def document_set_errors(
    document_set: DocumentSet,
    *,
    volume_codes: Optional[list[str]] = None,
    index: Optional[dict[str, dict]] = None,
    commit: str = "",
    report_code: str = "",
) -> list[str]:
    """Cross-checks against the volume manifest, the evidence index and ``--commit``."""
    errors: list[str] = []
    codes = [entry.code for entry in document_set.entries]
    if report_code and report_code in codes:
        errors.append(f"the document set lists the report itself (`{report_code}`); it lists only the other files")
    if volume_codes is not None:
        missing = [code for code in volume_codes if code not in codes]
        extra = [code for code in codes if code not in volume_codes]
        if missing:
            errors.append(f"the document set has no entry for volumes {missing} of the manifest")
        if extra:
            errors.append(f"the document set lists {extra}, which are not volumes of the manifest")
    if index is not None:
        for entry in document_set.entries:
            indexed = sum(1 for item in index.values() if item.get("volume") == entry.code)
            if indexed != entry.records:
                errors.append(
                    f"document `{entry.code}` says {entry.records} records but the evidence index lists {indexed}"
                )
    if commit:
        for owner, value in [("the document set", document_set.commit)] + [
            (f"document `{entry.code}`", entry.commit) for entry in document_set.entries
        ]:
            if value and not (value.startswith(commit) or commit.startswith(value)):
                errors.append(f"{owner} was built from commit {value}, not --commit {commit}")
    return errors


# ---------------------------------------------------------------------- hierarchy layout


def area_label(title: str) -> str:
    """A requirement file's area: its title without a trailing ``Requirements`` / ``Tests``."""
    label = re.sub(r"\s+(requirements|tests)\s*$", "", title.strip(), flags=re.IGNORECASE)
    return label or title.strip()


def split_ius_source(content: list[dict]) -> tuple[str, list[dict]]:
    """The ``IUS Source:`` text of a requirement (``""`` if none) and the content without it.

    Only a paragraph that holds nothing but that line is taken out.
    """
    source = ""
    kept: list[dict] = []
    for elem in content:
        if not source and elem.get("type") == "paragraph":
            lines = [line for line in _inline_text(elem).splitlines() if line.strip()]
            if len(lines) == 1:
                match = _IUS_SOURCE_LINE.match(lines[0])
                if match:
                    source = match.group(1).strip()
                    continue
        kept.append(elem)
    return source, kept


def _inline_text(node: dict) -> str:
    if node.get("type") in {"text", "codespan"}:
        return str(node.get("text", ""))
    if node.get("type") == "math":
        return math_source(node)
    if node.get("type") in {"softbreak", "linebreak"}:
        return "\n"
    return "".join(_inline_text(child) for child in node.get("children", []) or [])


def shell_has_manual_test(test: Test) -> bool:
    return any(
        elem.get("type") == "block_code" and str(elem.get("info") or "").strip().split()[:1] == ["manualtest"]
        for elem in test.content
    )


@dataclass
class RequirementSection:
    """A requirement statement and the shells printed under it."""

    requirement: Requirement
    area: str
    shells: list[Test] = field(default_factory=list)


@dataclass
class Tier1Chapter:
    tier1: Requirement
    # The Tier 1's own statement and IUS Source line, without the Source paragraph.
    ius_source: str
    statement: list[dict]
    # Shells whose requirements are all Tier 1 (the Tier 1 journey), printed first.
    journey_shells: list[Test] = field(default_factory=list)
    # Requirements whose statements are printed in this chapter, in corpus order.
    sections: list[RequirementSection] = field(default_factory=list)
    # Every requirement that traces to this Tier 1 (the derived-requirements table).
    derived: list[Requirement] = field(default_factory=list)


@dataclass
class HierarchyLayout:
    hierarchy: HierarchyConfig
    chapters: list[Tier1Chapter]
    # Requirements in procedural files (no Tier 1 parent), and their shells.
    procedural: list[RequirementSection]
    # Requirements that neither are Tier 1, nor trace to one, nor are procedural.
    outside: list[RequirementSection]
    # Shells that verify no requirement.
    loose_shells: list[Test]
    # Requirement ID -> area label (its file's title).
    areas: dict[str, str]
    # Requirement ID -> the Tier 1s it traces to, in chapter order.
    tier1_of: dict[str, list[str]]

    def chapter(self, tier1_id: str) -> Tier1Chapter:
        for chapter in self.chapters:
            if chapter.tier1.req_id == tier1_id:
                return chapter
        raise KeyError(tier1_id)

    def all_sections(self) -> list[RequirementSection]:
        return [section for chapter in self.chapters for section in chapter.sections] + self.procedural + self.outside

    def shell_ids(self) -> list[str]:
        """Every shell, in print order."""
        ids: list[str] = []
        for chapter in self.chapters:
            ids += [test.test_id for test in chapter.journey_shells]
            ids += [test.test_id for section in chapter.sections for test in section.shells]
        ids += [test.test_id for section in self.procedural + self.outside for test in section.shells]
        return ids + [test.test_id for test in self.loose_shells]


class HierarchyError(ValueError):
    pass


def plan_hierarchy(
    document: Document,
    hierarchy: Optional[HierarchyConfig] = None,
    *,
    tier1_order: Optional[list[str]] = None,
) -> HierarchyLayout:
    """Lay the corpus out by Tier 1. ``tier1_order`` (a volume manifest's) orders the chapters.

    A requirement tracing to several Tier 1s is printed in the first chapter and listed in
    the derived-requirements table of each. A shell is printed under the first requirement
    (in print order) it verifies that is not a Tier 1; a shell that verifies only Tier 1s is
    a journey shell of the first. Fails unless every requirement and shell lands exactly once.
    """
    config = hierarchy if hierarchy is not None else (document.hierarchy or HierarchyConfig())
    if not config.top_level_prefix:
        raise HierarchyError(
            "--structure hierarchy needs a Tier 1 prefix: set hierarchy.top_level_prefix in config.yml "
            "or pass a --volume-manifest with tier1_prefix"
        )
    requirements = document.requirements_by_id()
    areas = {item.req_id: area_label(doc.title) for doc in document.requirements for item in doc.items}
    corpus_tier1 = [item.req_id for item in document.tier1_requirements(config)]
    if not corpus_tier1:
        raise HierarchyError(f"--structure hierarchy found no Tier 1 requirement ({config.top_level_prefix}*)")
    order = [tier1_id for tier1_id in (tier1_order or []) if tier1_id in corpus_tier1]
    order += [tier1_id for tier1_id in corpus_tier1 if tier1_id not in order]
    rank = {tier1_id: position for position, tier1_id in enumerate(order)}

    chapters: dict[str, Tier1Chapter] = {}
    for tier1_id in order:
        tier1 = requirements[tier1_id]
        source, statement = split_ius_source(tier1.body_content())
        chapters[tier1_id] = Tier1Chapter(tier1=tier1, ius_source=source, statement=statement)

    procedural_ids = set(document.procedural_requirement_ids(config))
    procedural: list[RequirementSection] = []
    outside: list[RequirementSection] = []
    tier1_of: dict[str, list[str]] = {}
    chapter_sections: list[tuple[str, RequirementSection]] = []
    for doc in document.requirements:
        for requirement in doc.items:
            req_id = requirement.req_id
            if req_id in chapters:
                tier1_of[req_id] = [req_id]
                continue
            section = RequirementSection(requirement=requirement, area=areas[req_id])
            if req_id in procedural_ids:
                tier1_of[req_id] = []
                procedural.append(section)
                continue
            traced = sorted(document.tier1_ids_of(req_id, config), key=lambda tier1_id: rank.get(tier1_id, len(rank)))
            traced = [tier1_id for tier1_id in traced if tier1_id in chapters]
            tier1_of[req_id] = traced
            if not traced:
                outside.append(section)
                continue
            chapter_sections.append((traced[0], section))
            for tier1_id in traced:
                chapters[tier1_id].derived.append(requirement)
    for tier1_id, section in chapter_sections:
        chapters[tier1_id].sections.append(section)

    # Requirement ID -> (print position, section), for placing shells.
    placed: dict[str, tuple[int, RequirementSection]] = {}
    position = 0
    for chapter in chapters.values():
        for section in chapter.sections:
            placed[section.requirement.req_id] = (position, section)
            position += 1
    for section in procedural + outside:
        placed[section.requirement.req_id] = (position, section)
        position += 1

    loose: list[Test] = []
    for test_doc in document.tests:
        for test in test_doc.items:
            req_ids = [req_id for req_id in dict.fromkeys(test.req_ids) if req_id in requirements]
            targets = sorted((placed[req_id] for req_id in req_ids if req_id in placed), key=lambda item: item[0])
            if targets:
                targets[0][1].shells.append(test)
                continue
            tier1_ids = sorted({req_id for req_id in req_ids if req_id in chapters}, key=lambda t: rank[t])
            if tier1_ids:
                chapters[tier1_ids[0]].journey_shells.append(test)
                continue
            loose.append(test)

    layout = HierarchyLayout(
        hierarchy=config,
        chapters=list(chapters.values()),
        procedural=procedural,
        outside=outside,
        loose_shells=loose,
        areas=areas,
        tier1_of=tier1_of,
    )
    _check_layout_complete(document, layout)
    return layout


def _check_layout_complete(document: Document, layout: HierarchyLayout) -> None:
    printed = [chapter.tier1.req_id for chapter in layout.chapters]
    printed += [section.requirement.req_id for section in layout.all_sections()]
    expected = [item.req_id for doc in document.requirements for item in doc.items]
    shells = layout.shell_ids()
    expected_shells = [item.test_id for doc in document.tests for item in doc.items]
    problems: list[str] = []
    if sorted(printed) != sorted(expected):
        problems.append(f"requirements printed {len(printed)} times for {len(expected)} requirements")
    if sorted(shells) != sorted(expected_shells):
        problems.append(f"shells printed {len(shells)} times for {len(expected_shells)} shells")
    if problems:  # pragma: no cover - a defect in plan_hierarchy, not in the corpus
        raise HierarchyError("The hierarchy layout is incomplete: " + "; ".join(problems))


# ---------------------------------------------------------------------- CI run register


@dataclass
class CiRun:
    """One CI workflow run, from the records it produced."""

    workflow: str
    run_id: Optional[int]
    run_url: str
    head_shas: list[str] = field(default_factory=list)
    evidence_ids: list[str] = field(default_factory=list)
    statuses: list[str] = field(default_factory=list)
    # Status of the ``kind: workflow`` record for this run, when there is one.
    workflow_status: str = ""

    @property
    def in_ci(self) -> bool:
        return bool(self.run_id) or bool(self.run_url)

    @property
    def failed(self) -> int:
        return sum(1 for status in self.statuses if status.strip().lower() not in {"passed", "pass", "success"})

    @property
    def conclusion(self) -> str:
        if self.workflow_status:
            return self.workflow_status
        return "passed" if self.statuses and not self.failed else "failed"


def ci_run_register(records: Iterable["EvidenceRecord"]) -> list[CiRun]:
    """Each workflow run once, in first-seen order; records with no CI run are grouped by workflow."""
    runs: dict[tuple[str, object], CiRun] = {}
    for record in records:
        workflow = record.workflow_name or record.workflow_path or "(no workflow)"
        in_ci = bool(record.run_id) or bool(record.run_url)
        key: tuple[str, object] = (
            ("run", record.run_id or record.run_url) if in_ci else ("local", workflow)
        )
        run = runs.get(key)
        if run is None:
            run = CiRun(
                workflow=workflow, run_id=record.run_id if in_ci else None, run_url=record.run_url if in_ci else ""
            )
            runs[key] = run
        if record.head_sha and record.head_sha not in run.head_shas:
            run.head_shas.append(record.head_sha)
        if not run.run_url and record.run_url:
            run.run_url = record.run_url
        run.evidence_ids.append(record.evidence_id)
        run.statuses.append(record.status)
        if record.kind == "workflow":
            run.workflow_status = record.status
    return [run for run in runs.values() if run.in_ci] + [run for run in runs.values() if not run.in_ci]


# ---------------------------------------------------------------------- volumes


def short_volume_code(report_code: str, volume_code: str) -> str:
    """``BXD-VF-019-E03`` -> ``E03`` when it starts with the report code, else unchanged."""
    prefix = report_code + "-" if report_code else ""
    if prefix and volume_code.startswith(prefix) and len(volume_code) > len(prefix):
        return volume_code[len(prefix):]
    return volume_code


def requirement_volumes(
    requirement_ids: list[str],
    *,
    plan: Optional["VolumePlan"],
    evidence_ids: dict[str, list[str]],
    index: dict[str, dict],
) -> dict[str, list[str]]:
    """Requirement ID -> evidence volume code(s): the manifest's, else the indexed records'."""
    volumes: dict[str, list[str]] = {}
    for req_id in requirement_ids:
        found: list[str] = []
        if plan is not None and plan.requirement_volume.get(req_id):
            found.append(str(plan.requirement_volume[req_id]))
        else:
            for evidence_id in evidence_ids.get(req_id, []):
                volume = str(index.get(evidence_id, {}).get("volume", "") or "")
                if volume and volume not in found:
                    found.append(volume)
        volumes[req_id] = found
    return volumes
