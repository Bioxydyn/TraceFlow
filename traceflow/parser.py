import fnmatch
import os
import re
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Union, List, Generic, Optional, TypeVar, Callable, Type, TYPE_CHECKING

import mistune
import mistune.plugins
import yaml
from mistune.core import BlockState, InlineState

if TYPE_CHECKING:  # pragma: no cover - typing only, avoids an import cycle
    from traceflow.evidence import EvidenceRecord


# Only H2 headings whose first word matches this pattern become requirement / test / risk
# items. Any other H2 (``## Allowlisted gaps``) stays prose. ``config.yml`` can override it
# with ``item_id_pattern:``.
DEFAULT_ITEM_ID_PATTERN = r"^(REQ|TEST|RISK)-[A-Z0-9-]+"

# A ``Parent:`` (or ``**Parent:**`` / ``Parents:``) line inside a requirement.
_PARENT_LINE_PATTERN = re.compile(
    r"^\s*(?:\*\*|__)?\s*Parents?\s*(?:\*\*|__)?\s*:\s*(?:\*\*|__)?\s*(.*)$",
    re.IGNORECASE,
)
_ID_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


def _compile_item_id_pattern(pattern: Optional[str]) -> "re.Pattern[str]":
    raw = pattern or DEFAULT_ITEM_ID_PATTERN
    try:
        return re.compile(raw)
    except re.error as exc:
        raise ValueError(f"config.yml item_id_pattern `{raw}` is not a valid regular expression: {exc}") from exc


@dataclass
class Requirement:
    req_id: str
    content: list[dict]
    title: str
    test_ids: list[str]
    # Parsed from ``Parent:`` lines (de-duplicated, in order). The lines stay in ``content``
    # so the default rendering is unchanged; ``body_content()`` drops them.
    parent_ids: list[str] = field(default_factory=list)
    # Filled by ``Document``: requirements whose ``Parent:`` names this one, in corpus order.
    child_ids: list[str] = field(default_factory=list)
    # Filled by ``Document``: risks that cite this requirement directly or cite a test shell
    # that verifies it, in risk-register order.
    risk_ids: list[str] = field(default_factory=list)
    # ``Parent:`` lines that named no recognisable ID (reported by ``verify_hierarchy``).
    malformed_parent_lines: list[str] = field(default_factory=list)

    def body_content(self) -> list[dict]:
        """``content`` without the paragraphs that only hold ``Parent:`` lines."""
        return [elem for elem in self.content if not _is_parent_only_paragraph(elem)]


@dataclass
class AutotestResult:
    """Structured view of a per-test ``result.md`` file.

    The collector emits a metadata bullet list (``- Description: ...`` etc.) plus optional
    ``## Assertions``, ``## Screenshots``, and ``## Output`` sections. This dataclass holds the
    extracted parts so the PDF generator can render them as labelled blocks rather than a
    flat bullet list.

    ``record`` holds the typed evidence record (status, kind, run, figures with SHA-256, ...)
    when the result was loaded through ``traceflow.evidence.load_autotest_result``. It comes
    from ``result.json`` when present and from ``result.md`` otherwise.
    """

    test_id: str
    description: str
    assertions_items: list[dict] = field(default_factory=list)
    metadata_items: list[dict] = field(default_factory=list)
    other_items: list[dict] = field(default_factory=list)
    missing_fields: list[str] = field(default_factory=list)
    record: Optional["EvidenceRecord"] = None


@dataclass
class Test:
    test_id: str
    content: list[dict]
    title: str
    req_ids: list[str]
    # IDs from the shell's ``autotest`` blocks, de-duplicated, in order.
    evidence_ids: list[str] = field(default_factory=list)
    # Filled by ``Document``: risks whose register entry cites this shell.
    risk_ids: list[str] = field(default_factory=list)


@dataclass
class Risk:
    risk_id: str
    content: list[dict]
    title: str
    attributes: dict[str, str]
    requirement_refs: list[str]
    test_refs: list[str]


@dataclass
class MarkdownDocument:
    title: str
    filename: str
    content: list[dict]
    category: str

    @staticmethod
    def from_file(file_path: str, category: str, *, math: bool = False) -> 'MarkdownDocument':
        parsed = parse_markdown(read_file(file_path), math=math)
        title = extract_title(parsed)
        content = parsed[1:]
        return MarkdownDocument(title=title, filename=file_path, content=content, category=category)


T = TypeVar('T', bound=Union['Requirement', 'Test', 'Risk'])
C = TypeVar('C', bound='SubDocument')


@dataclass
class SubDocument(Generic[T]):
    title: str
    generic_content: list[dict]
    filename: str
    items: List[T]
    # Path relative to the input directory, with ``/`` separators (set by ``process_directory``).
    source_path: str = ""

    @staticmethod
    def from_file_impl(
        cls: Type[C],
        file_path: str,
        item_generator: Callable[[list[dict]], list[T]],
        item_id_pattern: Optional[str] = None,
        *,
        math: bool = False,
    ) -> C:
        content = read_file(file_path)
        parsed_content = parse_markdown(content, math=math)
        # Everything outside an item, except H1 headings, is the file's prose: the text before
        # the first item plus any non-item H2 section (``## Allowlisted gaps``), in order.
        generic_content, _ = split_items(parsed_content, item_id_pattern)
        title = extract_title(parsed_content)
        items = item_generator(parsed_content)

        return cls(title=title, generic_content=generic_content, filename=file_path, items=items)


def _item_id_from_heading(heading_text: str, pattern: "re.Pattern[str]") -> Optional[str]:
    """Return the item ID an H2 heading declares, or ``None`` if the heading is prose.

    A heading that nearly declares an ID (wrong case, ``_`` for ``-``, a trailing ``/002``)
    raises instead of silently turning a requirement or test into prose.
    """
    tokens = heading_text.split()
    if not tokens:
        return None
    token = tokens[0].replace(":", "")
    if pattern.fullmatch(token):
        return token
    has_digit = any(char.isdigit() for char in token)
    normalised = token.upper().replace("_", "-")
    if has_digit and (pattern.match(token) or pattern.fullmatch(normalised)):
        raise ValueError(
            f"Heading `## {heading_text}` looks like an item ID, but `{token}` does not match "
            f"the item ID pattern `{pattern.pattern}`"
        )
    return None


def split_items(
    parsed_content: list[dict], item_id_pattern: Optional[str] = None
) -> tuple[list[dict], list[tuple[str, str, list[dict]]]]:
    """Split a parsed file into its prose and its ``(item_id, title, content)`` items.

    Only H2 headings whose first word matches ``item_id_pattern`` start an item. Any other
    H2 ends the current item and is kept, with what follows it, as prose.
    """
    pattern = _compile_item_id_pattern(item_id_pattern)
    generic_content: list[dict] = []
    items: list[tuple[str, str, list[dict]]] = []
    current: Optional[list[dict]] = None
    for elem in parsed_content:
        level = is_ast_element_heading(elem)
        if level == 2:
            heading_text = get_heading_text(elem)
            item_id = _item_id_from_heading(heading_text, pattern)
            if item_id is not None:
                title = heading_text.replace(item_id + ":", "").strip()
                current = []
                items.append((item_id, title, current))
                continue
            current = None
            generic_content.append(elem)
            continue
        if current is not None:
            current.append(elem)
        elif level != 1:
            generic_content.append(elem)
    return generic_content, items


def _paragraph_lines(elem: dict) -> list[str]:
    if elem.get("type") != "paragraph":
        return []
    return [line for line in _flatten_inline_text(elem).splitlines() if line.strip()]


def _is_parent_only_paragraph(elem: dict) -> bool:
    lines = _paragraph_lines(elem)
    return bool(lines) and all(_PARENT_LINE_PATTERN.match(line) for line in lines)


def extract_parent_ids(content: list[dict], item_id_pattern: Optional[str] = None) -> tuple[list[str], list[str]]:
    """Return ``(parent_ids, malformed_lines)`` from a requirement's top-level paragraphs.

    Repeated parents (for example two identical ``Parent:`` lines) are listed once.
    """
    pattern = _compile_item_id_pattern(item_id_pattern)
    parent_ids: list[str] = []
    malformed: list[str] = []
    for elem in content:
        for line in _paragraph_lines(elem):
            match = _PARENT_LINE_PATTERN.match(line)
            if not match:
                continue
            ids = [token for token in _ID_TOKEN_PATTERN.findall(match.group(1)) if pattern.fullmatch(token)]
            if not ids:
                malformed.append(line.strip())
            for parent_id in ids:
                if parent_id not in parent_ids:
                    parent_ids.append(parent_id)
    return parent_ids, malformed


def extract_autotest_ids(content: list[dict]) -> list[str]:
    """IDs named by the top-level ``autotest`` code blocks of a test shell, in order, once each.

    Only top-level blocks are collected because only those are rendered.
    """
    evidence_ids: list[str] = []
    for elem in content:
        if elem.get("type") != "block_code":
            continue
        raw_info = str(elem.get("info") or "").strip()
        if not raw_info or raw_info.split()[0] != "autotest":
            continue
        for line in str(elem.get("text", "")).splitlines():
            evidence_id = line.strip()
            if evidence_id and evidence_id not in evidence_ids:
                evidence_ids.append(evidence_id)
    return evidence_ids


class RequirementDocument(SubDocument[Requirement]):
    @staticmethod
    def item_generator(parsed_content: list[dict], item_id_pattern: Optional[str] = None) -> list[Requirement]:
        requirements: list[Requirement] = []
        for req_id, title, content in split_items(parsed_content, item_id_pattern)[1]:
            parent_ids, malformed = extract_parent_ids(content, item_id_pattern)
            requirements.append(
                Requirement(
                    req_id=req_id,
                    content=content,
                    title=title,
                    test_ids=[],
                    parent_ids=parent_ids,
                    malformed_parent_lines=malformed,
                )
            )
        return requirements

    @staticmethod
    def from_file(
        file_path: str, *, item_id_pattern: Optional[str] = None, math: bool = False
    ) -> 'RequirementDocument':
        return SubDocument.from_file_impl(
            RequirementDocument,
            file_path,
            lambda parsed: RequirementDocument.item_generator(parsed, item_id_pattern),
            item_id_pattern,
            math=math,
        )


class TestDocument(SubDocument[Test]):

    @staticmethod
    def extract_requirement_ids(content_item: dict) -> list[str]:
        if content_item["type"] != "paragraph":
            return []
        children = content_item["children"]
        requirements = []
        for index in range(len(children)):
            child = children[index]

            if child["type"] == "strong":
                if child["children"][0]["text"] == "Requirement ID:":
                    # The text of the next element is the requirement ID
                    # Check that we have index +1
                    if index + 1 >= len(children):
                        print("Warning, Requirement ID not found, expcted one after `Requirement ID:`")
                    else:
                        full_req = children[index + 1]["text"].strip().split()[0].split(":")[0]
                        requirements.append(full_req)
        return requirements

    @staticmethod
    def item_generator(parsed_content: list[dict], item_id_pattern: Optional[str] = None) -> list[Test]:
        tests: list[Test] = []
        for test_id, title, content in split_items(parsed_content, item_id_pattern)[1]:
            req_ids: list[str] = []
            for elem in content:
                req_ids += TestDocument.extract_requirement_ids(elem)
            tests.append(
                Test(
                    test_id=test_id,
                    content=content,
                    title=title,
                    req_ids=req_ids,
                    evidence_ids=extract_autotest_ids(content),
                )
            )
        return tests

    @staticmethod
    def from_file(
        file_path: str, *, item_id_pattern: Optional[str] = None, math: bool = False
    ) -> 'TestDocument':
        return SubDocument.from_file_impl(
            TestDocument,
            file_path,
            lambda parsed: TestDocument.item_generator(parsed, item_id_pattern),
            item_id_pattern,
            math=math,
        )


class RiskDocument(SubDocument[Risk]):

    _FIELD_ALIASES: dict[str, str] = {
        "hazardous situation": "hazardous_situation",
        "hazard": "hazardous_situation",
        "harm": "harm",
        "cause": "cause",
        "severity": "severity",
        "probability": "probability",
        "risk estimate": "risk_estimate",
        "risk estimation": "risk_estimate",
        "controls": "controls",
        "risk control": "controls",
        "risk controls": "controls",
        "risk control measure": "controls",
        "residual risk": "residual_risk",
        "residual risk assessment": "residual_risk",
        "residual severity": "residual_severity",
        "residual probability": "residual_probability",
        "test evidence": "test_evidence",
        "detection": "detection",
        "linked requirement": "linked_requirements",
        "linked requirements": "linked_requirements",
        "linked test": "linked_tests",
        "linked tests": "linked_tests",
    }

    _REQ_PATTERN = re.compile(r"\bREQ-[A-Za-z0-9_-]+\b", re.IGNORECASE)
    _TEST_PATTERN = re.compile(r"\bTEST-[A-Za-z0-9_-]+\b", re.IGNORECASE)

    @staticmethod
    def _normalise_field_name(raw: str) -> str | None:
        normalised = re.sub(r"[^a-z0-9 ]", "", raw.lower().strip())
        return RiskDocument._FIELD_ALIASES.get(normalised)

    @staticmethod
    def _flatten_text(elem: dict) -> str:
        if elem["type"] == "text":
            return elem.get("text", "")
        if elem["type"] == "math":
            return math_source(elem)
        if elem["type"] == "codespan":
            return elem.get("text", "")

        if elem["type"] in {"softbreak", "linebreak"}:
            return "\n"

        text = ""
        for child in elem.get("children", []):
            text += RiskDocument._flatten_text(child)
        return text

    @staticmethod
    def _extract_field(elem: dict) -> tuple[str, str] | None:
        if elem["type"] != "paragraph":
            return None
        text = RiskDocument._flatten_text(elem).strip()
        if ":" not in text:
            return None
        field_name, value = text.split(":", 1)
        canonical = RiskDocument._normalise_field_name(field_name)
        if canonical is None:
            return None
        return canonical, value.strip()

    @staticmethod
    def _extract_references(text: str) -> tuple[list[str], list[str]]:
        reqs = [match.upper() for match in RiskDocument._REQ_PATTERN.findall(text)]
        tests = [match.upper() for match in RiskDocument._TEST_PATTERN.findall(text)]
        return reqs, tests

    @staticmethod
    def item_generator(parsed_content: list[dict], item_id_pattern: Optional[str] = None) -> list[Risk]:
        risks: list[Risk] = []
        for risk_id, title, content in split_items(parsed_content, item_id_pattern)[1]:
            current_risk = Risk(
                risk_id=risk_id,
                content=[],
                title=title,
                attributes={},
                requirement_refs=[],
                test_refs=[],
            )
            for elem in content:
                current_risk.content.append(elem)
                extracted_field = RiskDocument._extract_field(elem)
                flattened = RiskDocument._flatten_text(elem)
                req_refs, test_refs = RiskDocument._extract_references(flattened)
                for req in req_refs:
                    if req not in current_risk.requirement_refs:
                        current_risk.requirement_refs.append(req)
                for test in test_refs:
                    if test not in current_risk.test_refs:
                        current_risk.test_refs.append(test)
                if extracted_field:
                    field_name, field_value = extracted_field
                    current_risk.attributes[field_name] = field_value
                    # If the field explicitly lists linked requirements/tests, merge them back in
                    if field_name in {"linked_requirements", "controls"}:
                        reqs_in_field, _ = RiskDocument._extract_references(field_value)
                        for req in reqs_in_field:
                            if req not in current_risk.requirement_refs:
                                current_risk.requirement_refs.append(req)
                    if field_name in {"linked_tests", "controls"}:
                        _, tests_in_field = RiskDocument._extract_references(field_value)
                        for test in tests_in_field:
                            if test not in current_risk.test_refs:
                                current_risk.test_refs.append(test)
            risks.append(current_risk)
        return risks

    @staticmethod
    def from_file(
        file_path: str, *, item_id_pattern: Optional[str] = None, math: bool = False
    ) -> 'RiskDocument':
        return SubDocument.from_file_impl(
            RiskDocument,
            file_path,
            lambda parsed: RiskDocument.item_generator(parsed, item_id_pattern),
            item_id_pattern,
            math=math,
        )


@dataclass
class HierarchyConfig:
    """``config.yml`` ``hierarchy:`` block. Its presence turns on ``Document.verify_hierarchy``.

    - ``top_level_prefix``: requirement IDs starting with it are Tier 1 (``REQ-VF-T1-``).
      Every other requirement, outside the procedural files, must then trace to a Tier 1.
    - ``require_single_parent``: such a requirement must have exactly one parent, a Tier 1.
    - ``procedural_files``: globs (matched against the path relative to the input directory,
      or the file name) for files whose requirements have no parent.
    """

    top_level_prefix: str = ""
    require_single_parent: bool = False
    procedural_files: list[str] = field(default_factory=list)

    _KEYS = ("top_level_prefix", "require_single_parent", "procedural_files")

    @classmethod
    def from_config(cls: type['HierarchyConfig'], raw: Optional[dict]) -> 'HierarchyConfig':
        if raw is None:
            raw = {}
        if not isinstance(raw, dict):
            raise ValueError("config.yml `hierarchy:` must be a mapping")
        unknown = sorted(set(raw) - set(cls._KEYS))
        if unknown:
            raise ValueError(f"config.yml `hierarchy:` has unknown keys: {unknown} (known: {list(cls._KEYS)})")
        prefix = raw.get("top_level_prefix") or ""
        if not isinstance(prefix, str):
            raise ValueError("config.yml `hierarchy.top_level_prefix` must be a string")
        single = raw.get("require_single_parent", False)
        if not isinstance(single, bool):
            raise ValueError("config.yml `hierarchy.require_single_parent` must be true or false")
        procedural = raw.get("procedural_files") or []
        if not isinstance(procedural, list) or not all(isinstance(item, str) for item in procedural):
            raise ValueError("config.yml `hierarchy.procedural_files` must be a list of file globs")
        if single and not prefix:
            raise ValueError("config.yml `hierarchy.require_single_parent` needs `hierarchy.top_level_prefix`")
        return cls(top_level_prefix=prefix, require_single_parent=single, procedural_files=list(procedural))


@dataclass
class EvidenceBinding:
    """Where one evidence ID (an ``autotest`` result) is used: every shell that binds it, the
    requirements those shells verify, and the Tier 1s above those requirements."""

    evidence_id: str
    shell_ids: list[str] = field(default_factory=list)
    requirement_ids: list[str] = field(default_factory=list)
    tier1_ids: list[str] = field(default_factory=list)


def path_matches(relative_path: str, patterns: list[str]) -> bool:
    """True if a corpus-relative POSIX path, or its file name, matches any glob."""
    name = PurePosixPath(relative_path).name
    return any(
        fnmatch.fnmatchcase(relative_path, pattern) or fnmatch.fnmatchcase(name, pattern)
        for pattern in patterns
    )


def _append_unique(target: list[str], value: str) -> None:
    if value not in target:
        target.append(value)


@dataclass
class Document:
    requirements: list[RequirementDocument]
    tests: list[TestDocument]
    risks: list[RiskDocument]
    design_documents: list[MarkdownDocument]
    supplementary_documents: list[MarkdownDocument]
    name: str = ""
    input_dir: str = ""
    version: str = ""
    # ``config.yml`` ``hierarchy:`` block; ``None`` leaves the hierarchy unchecked.
    hierarchy: Optional[HierarchyConfig] = None
    # The raw ``config.yml`` mapping (empty when there is none).
    config: dict = field(default_factory=dict)

    def verify_all_ids_unique(self) -> None:
        """ Check if all IDs (both test and requirement) are unique """
        all_ids: list[str] = (
            [req.req_id for r in self.requirements for req in r.items]
            + [test.test_id for t in self.tests for test in t.items]
            + [risk.risk_id for risk_doc in self.risks for risk in risk_doc.items]
        )

        if len(all_ids) != len(set(all_ids)):
            # There are duplicates - what are they?
            duplicates = [i for i in all_ids if all_ids.count(i) > 1]
            raise ValueError(f"Duplicate IDs found: {duplicates}")

    def build_traceability_matrices(self) -> None:
        for requirement_doc in self.requirements:
            for r in requirement_doc.items:
                r_id = r.req_id
                r.test_ids = []
                for test_doc in self.tests:
                    for t in test_doc.items:
                        if r_id in t.req_ids:
                            r.test_ids.append(t.test_id)

        all_requirement_ids: set[str] = {item.req_id for r in self.requirements for item in r.items}

        # Iterate over the test documents and verify that any referenced requirements exist
        for test_doc in self.tests:
            for t in test_doc.items:
                for req_id in t.req_ids:
                    if req_id not in all_requirement_ids:
                        raise ValueError(f"Test `{t.test_id}` references requirement `{req_id}` which does not exist")

    def verify_risk_references(self) -> None:
        """Ensure that any requirement/test IDs referenced within the risk register exist."""
        known_requirements: set[str] = {item.req_id for doc in self.requirements for item in doc.items}
        known_tests: set[str] = {item.test_id for doc in self.tests for item in doc.items}
        for risk_doc in self.risks:
            for risk in risk_doc.items:
                for req_id in risk.requirement_refs:
                    if req_id not in known_requirements:
                        raise ValueError(
                            f"Risk `{risk.risk_id}` references requirement `{req_id}` which does not exist"
                        )
                for test_id in risk.test_refs:
                    if test_id not in known_tests:
                        raise ValueError(f"Risk `{risk.risk_id}` references test `{test_id}` which does not exist")

    # ------------------------------------------------------------------ lookups

    def requirements_by_id(self) -> dict[str, Requirement]:
        return {item.req_id: item for doc in self.requirements for item in doc.items}

    def tests_by_id(self) -> dict[str, Test]:
        return {item.test_id: item for doc in self.tests for item in doc.items}

    def risks_by_id(self) -> dict[str, Risk]:
        return {item.risk_id: item for doc in self.risks for item in doc.items}

    def requirement_document_of(self, req_id: str) -> RequirementDocument:
        for doc in self.requirements:
            if any(item.req_id == req_id for item in doc.items):
                return doc
        raise KeyError(req_id)

    def test_document_of(self, test_id: str) -> TestDocument:
        for doc in self.tests:
            if any(item.test_id == test_id for item in doc.items):
                return doc
        raise KeyError(test_id)

    def relative_path(self, filename: str) -> str:
        """A source file's path relative to ``input_dir``, with ``/`` separators."""
        base = self.input_dir or "."
        return PurePosixPath(*Path(os.path.relpath(os.path.abspath(filename), os.path.abspath(base))).parts).as_posix()

    # ---------------------------------------------------------------- hierarchy

    def build_hierarchy_links(self) -> None:
        """Fill ``Requirement.child_ids`` from the parsed ``Parent:`` lines."""
        index = self.requirements_by_id()
        for requirement in index.values():
            requirement.child_ids = []
        for requirement in index.values():
            for parent_id in requirement.parent_ids:
                parent = index.get(parent_id)
                if parent is not None and parent is not requirement:
                    _append_unique(parent.child_ids, requirement.req_id)

    def build_risk_links(self) -> None:
        """Fill ``Requirement.risk_ids`` and ``Test.risk_ids`` from the risk register.

        A requirement is linked to a risk that cites it, or that cites a shell verifying it.
        """
        requirements = self.requirements_by_id()
        tests = self.tests_by_id()
        for requirement in requirements.values():
            requirement.risk_ids = []
        for test in tests.values():
            test.risk_ids = []
        for risk_doc in self.risks:
            for risk in risk_doc.items:
                for req_id in risk.requirement_refs:
                    if req_id in requirements:
                        _append_unique(requirements[req_id].risk_ids, risk.risk_id)
                for test_id in risk.test_refs:
                    test = tests.get(test_id)
                    if test is None:
                        continue
                    _append_unique(test.risk_ids, risk.risk_id)
                    for req_id in test.req_ids:
                        if req_id in requirements:
                            _append_unique(requirements[req_id].risk_ids, risk.risk_id)

    def _effective_hierarchy(self, config: Optional[HierarchyConfig]) -> HierarchyConfig:
        if config is not None:
            return config
        return self.hierarchy if self.hierarchy is not None else HierarchyConfig()

    def is_tier1(self, req_id: str, config: Optional[HierarchyConfig] = None) -> bool:
        prefix = self._effective_hierarchy(config).top_level_prefix
        return bool(prefix) and req_id.startswith(prefix)

    def tier1_requirements(self, config: Optional[HierarchyConfig] = None) -> list[Requirement]:
        """Tier 1 requirements in corpus order (empty without a ``top_level_prefix``)."""
        return [item for doc in self.requirements for item in doc.items if self.is_tier1(item.req_id, config)]

    def procedural_requirement_ids(self, config: Optional[HierarchyConfig] = None) -> list[str]:
        """Requirements in the files the hierarchy config lists as procedural."""
        patterns = self._effective_hierarchy(config).procedural_files
        if not patterns:
            return []
        return [
            item.req_id
            for doc in self.requirements
            if path_matches(doc.source_path or self.relative_path(doc.filename), patterns)
            for item in doc.items
        ]

    def tier1_ids_of(self, req_id: str, config: Optional[HierarchyConfig] = None) -> list[str]:
        """The Tier 1 requirement(s) a requirement traces to through ``Parent:`` lines.

        A Tier 1 traces to itself. The walk is cycle-safe, and the result is in the order the
        ancestors are reached.
        """
        index = self.requirements_by_id()
        found: list[str] = []
        seen: set[str] = set()
        pending = [req_id]
        while pending:
            current = pending.pop(0)
            if current in seen:
                continue
            seen.add(current)
            if self.is_tier1(current, config):
                _append_unique(found, current)
                continue
            requirement = index.get(current)
            if requirement is not None:
                pending.extend(requirement.parent_ids)
        return found

    def hierarchy_errors(self, config: Optional[HierarchyConfig] = None) -> list[str]:
        """Every hierarchy problem, as messages. ``verify_hierarchy`` raises on any of them."""
        effective = self._effective_hierarchy(config)
        index = self.requirements_by_id()
        procedural = set(self.procedural_requirement_ids(effective))
        errors: list[str] = []
        for requirement in index.values():
            for line in requirement.malformed_parent_lines:
                errors.append(f"Requirement `{requirement.req_id}` has a Parent line that names no ID: `{line}`")
            for parent_id in requirement.parent_ids:
                if parent_id == requirement.req_id:
                    errors.append(f"Requirement `{requirement.req_id}` names itself as its parent")
                elif parent_id not in index:
                    errors.append(
                        f"Requirement `{requirement.req_id}` names parent `{parent_id}`, which is not a requirement"
                    )
        errors.extend(self._parent_cycle_errors(index))

        prefix = effective.top_level_prefix
        if prefix:
            for requirement in index.values():
                req_id = requirement.req_id
                parents = requirement.parent_ids
                if req_id in procedural:
                    if parents:
                        errors.append(
                            f"Requirement `{req_id}` is in a procedural file and must not name a parent "
                            f"(names {', '.join(parents)})"
                        )
                    continue
                if self.is_tier1(req_id, effective):
                    if parents:
                        errors.append(
                            f"Tier 1 requirement `{req_id}` must not name a parent (names {', '.join(parents)})"
                        )
                    continue
                if any(parent_id not in index or parent_id == req_id for parent_id in parents):
                    continue  # already reported as an unknown parent or a self-parent
                if effective.require_single_parent:
                    if len(parents) != 1 or not self.is_tier1(parents[0], effective):
                        named = ", ".join(parents) if parents else "none"
                        errors.append(
                            f"Requirement `{req_id}` must have exactly one Tier 1 parent ({prefix}*); it names: {named}"
                        )
                elif not self.tier1_ids_of(req_id, effective):
                    errors.append(f"Requirement `{req_id}` does not trace to any Tier 1 requirement ({prefix}*)")
        return errors

    @staticmethod
    def _parent_cycle_errors(index: dict[str, Requirement]) -> list[str]:
        """One message per distinct cycle in the parent graph (self-parents are reported apart)."""
        errors: list[str] = []
        reported: set[tuple[str, ...]] = set()
        state: dict[str, int] = {}  # 1 = on the current path, 2 = finished

        def visit(req_id: str, path: list[str]) -> None:
            state[req_id] = 1
            path.append(req_id)
            for parent_id in index[req_id].parent_ids:
                if parent_id == req_id or parent_id not in index:
                    continue
                if state.get(parent_id) == 1:
                    cycle = path[path.index(parent_id):]
                    start = cycle.index(min(cycle))
                    key = tuple(cycle[start:] + cycle[:start])
                    if key not in reported:
                        reported.add(key)
                        errors.append("Parent cycle: " + " -> ".join(key + (key[0],)))
                elif parent_id not in state:
                    visit(parent_id, path)
            path.pop()
            state[req_id] = 2

        for req_id in index:
            if req_id not in state:
                visit(req_id, [])
        return errors

    def verify_hierarchy(self, config: Optional[HierarchyConfig] = None) -> None:
        """Fail on an unknown parent, a self-parent, a cycle, a malformed Parent line and, with
        a ``top_level_prefix``, a requirement that does not trace to exactly one Tier 1 (or, with
        ``require_single_parent``, does not have exactly one Tier 1 parent)."""
        errors = self.hierarchy_errors(config)
        if errors:
            raise ValueError("Requirement hierarchy check failed:\n  - " + "\n  - ".join(errors))

    # ----------------------------------------------------------------- evidence

    def evidence_bindings(self, config: Optional[HierarchyConfig] = None) -> dict[str, EvidenceBinding]:
        """Map each evidence ID to the shells that bind it, their requirements and Tier 1s.

        Keys are in the order the IDs first appear in the corpus.
        """
        requirement_ids = set(self.requirements_by_id())
        bindings: dict[str, EvidenceBinding] = {}
        for test_doc in self.tests:
            for test in test_doc.items:
                for evidence_id in test.evidence_ids:
                    binding = bindings.setdefault(evidence_id, EvidenceBinding(evidence_id=evidence_id))
                    _append_unique(binding.shell_ids, test.test_id)
                    for req_id in test.req_ids:
                        _append_unique(binding.requirement_ids, req_id)
        for binding in bindings.values():
            for req_id in binding.requirement_ids:
                if req_id in requirement_ids:
                    for tier1_id in self.tier1_ids_of(req_id, config):
                        _append_unique(binding.tier1_ids, tier1_id)
        return bindings

    def __post_init__(self) -> None:
        self.verify_all_ids_unique()
        self.build_traceability_matrices()
        self.verify_risk_references()
        self.build_hierarchy_links()
        self.build_risk_links()
        if self.hierarchy is not None:
            self.verify_hierarchy()


def read_file(file_path: str) -> str:
    with open(file_path, "r") as file:
        return file.read()


# The characters a backslash escapes. Any other backslash is kept: \-, \., \d, C:\Users.
_ESCAPABLE = r"""[\\`*_<>\[\]()#|!~'"]"""
_UNESCAPE = re.compile(r"\\(" + _ESCAPABLE + ")")


class _LiteralInlineParser(mistune.InlineParser):
    r"""Inline Markdown that cannot eat the characters of a file name, key or path.

    ``_`` never starts emphasis (``_act with _ref`` once printed as italics and lost its
    underscores; ``'._'`` lost its ``_``): emphasis is ``*`` only. A backslash escapes only a
    character Markdown gives a meaning to, so ``\-``, ``\.``, ``\d`` or ``C:\Users\.x``
    keep their backslash.
    """

    SPECIFICATION = {
        **mistune.InlineParser.SPECIFICATION,
        "emphasis": r"\*+",
        "escape": r"(?:\\" + _ESCAPABLE + ")+",
    }
    UNESCAPE = _UNESCAPE

    def parse_escape(self, m: re.Match[str], state: InlineState) -> int:
        self.process_text(self.UNESCAPE.sub(r"\1", m.group(0)), state, parse_emphasis=False)
        return m.end()

    def process_text(self, text: str, state: InlineState, parse_emphasis: bool = True) -> None:
        # mistune 3 pairs emphasis delimiters after scanning, over all text: mark each run of
        # "_" as text the emphasis pass must not use.
        if not parse_emphasis or "_" not in text:
            super().process_text(text, state, parse_emphasis)
            return
        for piece in re.split(r"(_+)", text):
            if piece:
                super().process_text(piece, state, parse_emphasis=not piece.startswith("_"))


# Maths, read only when config.yml sets ``math: true``. ``$$...$$`` is display maths. ``$...$`` is
# inline maths by pandoc's rule: the opening $ is followed by a non-space, and the closing $
# follows a non-space and is not followed by a digit, so "$20 and $30" stays text. ``\$`` is a
# literal dollar, and a dollar inside the maths is written ``\$`` too.
_MATH = (
    r"\$\$(?P<display_math>(?:\\.|[^$\\])+?)\$\$"
    r"|\$(?![\s$])(?P<inline_math>(?:\\.|[^$\\])+?)(?<!\s)\$(?!\d)"
)
_MATH_ESCAPABLE = r"""[\\`*_<>\[\]()#|!~'"$]"""


def _check_math_braces(text: str) -> None:
    depth = 0
    for char in re.sub(r"\\.", "", text):
        depth += {"{": 1, "}": -1}.get(char, 0)
        if depth < 0:
            break
    if depth:
        raise ValueError(f"The maths `{text}` has unbalanced braces")


def _parse_math(inline: mistune.InlineParser, m: re.Match[str], state: InlineState) -> int:
    display = m.group("display_math")
    text = display if display is not None else m.group("inline_math")
    _check_math_braces(text)
    state.append_token({"type": "math", "raw": text, "display": display is not None})
    return m.end()


class _MathInlineParser(_LiteralInlineParser):
    """``_LiteralInlineParser`` that also reads ``$...$`` and ``$$...$$`` maths, and ``\\$``."""

    SPECIFICATION = {**_LiteralInlineParser.SPECIFICATION, "escape": r"(?:\\" + _MATH_ESCAPABLE + ")+"}
    UNESCAPE = re.compile(r"\\(" + _MATH_ESCAPABLE + ")")

    def __init__(self) -> None:
        super().__init__()
        self.register("math", _MATH, _parse_math, before="escape", start_chars="$")


def _blank_lines_after(src: str, end_pos: int) -> int:
    """Blank lines between the last text line before ``end_pos`` and the next text (or the end)."""
    start = end_pos
    while start > 0 and src[start - 1] in " \t\n":
        start -= 1
    stop = end_pos
    while stop < len(src) and src[stop] in " \t\n":
        stop += 1
    return max(src.count("\n", start, stop) - 1, 0)


class _LegacyBlockParser(mistune.BlockParser):
    """Records the blank lines after an indented code block, which mistune 2 kept in its text."""

    def parse_indent_code(self, m: re.Match[str], state: BlockState) -> int:
        count = len(state.tokens)
        end_pos = super().parse_indent_code(m, state)
        self._note_blank_lines(state, count, end_pos)
        return end_pos

    def _parse_indent_code_fast(self, state: BlockState) -> Optional[int]:
        count = len(state.tokens)
        end_pos = super()._parse_indent_code_fast(state)
        if end_pos is not None:
            self._note_blank_lines(state, count, end_pos)
        return end_pos

    @staticmethod
    def _note_blank_lines(state: BlockState, count: int, end_pos: int) -> None:
        if len(state.tokens) > count and state.tokens[-1].get("style") == "indent":
            state.tokens[-1]["_blank_lines_after"] = _blank_lines_after(state.src, end_pos)


def _plain_text(tokens: list[dict]) -> str:
    return "".join(
        token.get("raw", "\n" if token["type"] == "softbreak" else "") + _plain_text(token.get("children", []))
        for token in tokens
    )


def _legacy_ast(tokens: list[dict], level: int = 1) -> list[dict]:
    """mistune 3 tokens in the AST shape TraceFlow reads (mistune 2's), with mistune 2's output.

    Text runs and soft line breaks are one ``text`` node; attributes are flat; blank lines
    are dropped. mistune 2's readings are kept so documents render unchanged: a code span is
    trimmed at both edges, an empty fenced block holds one newline, an indented block ends
    with a newline and one more per blank line after it, and an ordered list has a ``start``
    only when it does not start at 1.
    """
    out: list[dict] = []
    for token in tokens:
        kind = token["type"]
        attrs = token.get("attrs", {})
        if kind == "blank_line":
            continue
        if kind in ("text", "softbreak"):
            text = token["raw"] if kind == "text" else "\n"
            if out and out[-1]["type"] == "text":
                out[-1]["text"] += text
            else:
                out.append({"type": "text", "text": text})
            continue
        if kind == "math":
            out.append({"type": "math", "text": token["raw"], "display": token["display"]})
            continue
        if kind == "image":
            out.append({"type": "image", "src": attrs["url"], "alt": _plain_text(token.get("children", [])),
                        "title": attrs.get("title")})
            continue
        node: dict = {"type": kind}
        if kind == "codespan":
            node["text"] = token["raw"].strip()
        elif kind in ("inline_html", "block_html"):
            node["text"] = token["raw"]
        elif kind == "block_code":
            if token.get("style") == "indent":
                node["text"] = token["raw"] + "\n" * (1 + token.get("_blank_lines_after", 0))
            else:
                node["text"] = token["raw"] or "\n"
            node["info"] = attrs.get("info")
        elif kind == "heading":
            node["level"] = attrs["level"]
        elif kind == "link":
            node["link"] = attrs["url"]
            node["title"] = attrs.get("title")
        elif kind == "list":
            node["ordered"] = attrs["ordered"]
            node["level"] = attrs["depth"] + 1
            if attrs["ordered"] and attrs.get("start") is not None:
                node["start"] = attrs["start"]
        elif kind == "list_item":
            node["level"] = level
        elif kind == "table_cell":
            node["align"] = attrs.get("align")
            node["is_head"] = attrs.get("head", False)
        if "children" in token:
            child_level = attrs["depth"] + 1 if kind == "list" else level
            children = token["children"]
            if kind in _LITERAL_MATH_PARENTS:
                children = [_math_as_text(child) for child in children]
            node["children"] = _legacy_ast(children, child_level)
        out.append(node)
    return out


# Headings, emphasis and struck-out text print their text, not their child nodes: maths in them
# stays as written, so headings (and the item IDs read from them) parse as they do without maths.
_LITERAL_MATH_PARENTS = {"heading", "emphasis", "strong", "strikethrough"}


def math_source(node: dict) -> str:
    """A math node (or mistune token) as the Markdown it was read from."""
    fence = "$$" if node["display"] else "$"
    return fence + node.get("text", node.get("raw", "")) + fence


def _math_as_text(token: dict) -> dict:
    return {"type": "text", "raw": math_source(token)} if token["type"] == "math" else token


_MARKDOWN = mistune.Markdown(
    renderer=None,
    block=_LegacyBlockParser(),
    inline=_LiteralInlineParser(),
    plugins=[mistune.plugins.import_plugin("table"), mistune.plugins.import_plugin("strikethrough")],
)


# A heading's hashes followed by a non-breaking space (``##\u00a0REQ-001: ...``) still start a
# heading, as they did with mistune 2; CommonMark only takes a space or a tab there.
_NBSP_HEADING = re.compile(r"^( {0,3}#{1,6})\u00a0", re.MULTILINE)


_MATH_MARKDOWN = mistune.Markdown(
    renderer=None,
    block=_LegacyBlockParser(),
    inline=_MathInlineParser(),
    plugins=[mistune.plugins.import_plugin("table"), mistune.plugins.import_plugin("strikethrough")],
)


def parse_markdown(content: str, *, math: bool = False) -> list[dict]:
    """The Markdown AST TraceFlow reads. ``math`` reads ``$...$`` and ``$$...$$`` as maths."""
    markdown = _MATH_MARKDOWN if math else _MARKDOWN
    tokens = markdown(_NBSP_HEADING.sub(r"\1 ", content))
    assert isinstance(tokens, list)
    return _legacy_ast(tokens)


def load_config(directory: str) -> dict:
    """Read ``<directory>/config.yml``; an absent or empty file is ``{}``."""
    config_file_path = os.path.join(os.path.abspath(directory), "config.yml")
    if not os.path.exists(config_file_path):
        return {}
    with open(config_file_path, "r") as config_file:
        config = yaml.safe_load(config_file)
    if config is None:
        return {}
    if not isinstance(config, dict):
        raise ValueError(f"{config_file_path} must hold a mapping")
    return config


def _sort_key(relative_path: str) -> tuple[str, str]:
    return relative_path.casefold(), relative_path


def ordered_markdown_files(directory: str, order: Optional[list[str]] = None) -> list[str]:
    """Every ``.md`` file under ``directory``, as corpus-relative POSIX paths.

    Files are sorted by path (case-insensitively, so the order does not depend on the file
    system). An ``order:`` list of globs from ``config.yml`` moves matching files to the
    front, in list order; a glob that matches nothing is an error.
    """
    relative_paths: list[str] = []
    for root, dirs, files in os.walk(directory):
        dirs.sort(key=lambda name: (name.casefold(), name))
        for file in files:
            if file.endswith(".md"):
                relative = os.path.relpath(os.path.join(root, file), directory)
                relative_paths.append(PurePosixPath(*Path(relative).parts).as_posix())
    relative_paths.sort(key=_sort_key)
    if not order:
        return relative_paths
    if not isinstance(order, list) or not all(isinstance(entry, str) for entry in order):
        raise ValueError("config.yml `order:` must be a list of file globs")
    unmatched = [entry for entry in order if not any(path_matches(path, [entry]) for path in relative_paths)]
    if unmatched:
        raise ValueError(f"config.yml `order:` entries match no Markdown file: {unmatched}")

    def rank(relative_path: str) -> int:
        for position, entry in enumerate(order):
            if path_matches(relative_path, [entry]):
                return position
        return len(order)

    return sorted(relative_paths, key=lambda path: (rank(path),) + _sort_key(path))


def process_directory(directory: str, version: str) -> Document:
    requirements = []
    tests = []
    risks = []
    design_documents: list[MarkdownDocument] = []
    supplementary_documents: list[MarkdownDocument] = []
    config = load_config(directory)
    item_id_pattern = config.get("item_id_pattern")
    if item_id_pattern is not None and not isinstance(item_id_pattern, str):
        raise ValueError("config.yml `item_id_pattern` must be a string")
    _compile_item_id_pattern(item_id_pattern)
    hierarchy = HierarchyConfig.from_config(config["hierarchy"]) if "hierarchy" in config else None
    # ``math: true`` reads $...$ and $$...$$ in the documents as maths; off by default, so a "$"
    # prints as written. Test evidence is never read as maths.
    math = config.get("math", False)
    if not isinstance(math, bool):
        raise ValueError("config.yml `math` must be true or false")

    for relative_path in ordered_markdown_files(directory, config.get("order")):
        file_path = os.path.join(directory, *PurePosixPath(relative_path).parts)
        path_components = [part.lower() for part in PurePosixPath(relative_path).parts]
        top_component = path_components[0]
        filename_component = path_components[-1]

        if top_component.startswith("req") or filename_component.startswith("req"):
            requirement_doc = RequirementDocument.from_file(file_path, item_id_pattern=item_id_pattern, math=math)
            requirement_doc.source_path = relative_path
            requirements.append(requirement_doc)
        elif top_component.startswith("test") or filename_component.startswith("test"):
            test_doc = TestDocument.from_file(file_path, item_id_pattern=item_id_pattern, math=math)
            test_doc.source_path = relative_path
            tests.append(test_doc)
        elif (
            top_component.startswith("risk")
            or filename_component.startswith("risk")
            or top_component.startswith("hazard")
            or filename_component.startswith("hazard")
        ):
            risk_doc = RiskDocument.from_file(file_path, item_id_pattern=item_id_pattern, math=math)
            risk_doc.source_path = relative_path
            risks.append(risk_doc)
        elif top_component.startswith("design") or filename_component.startswith("design"):
            design_documents.append(MarkdownDocument.from_file(file_path, category="design", math=math))
        else:
            supplementary_documents.append(MarkdownDocument.from_file(file_path, category="general", math=math))
    absolute_dir_path = os.path.abspath(directory)

    name = config.get("name", directory)
    return Document(
        requirements=requirements,
        tests=tests,
        risks=risks,
        design_documents=design_documents,
        supplementary_documents=supplementary_documents,
        name=name,
        input_dir=absolute_dir_path,
        version=version,
        hierarchy=hierarchy,
        config=config,
    )


def is_ast_element_heading(elem: dict) -> int:
    """ Returns 0 if NOT a heading, else returns the level"""
    if elem["type"] != "heading":
        return 0
    return elem["level"]


def get_heading_text(elem: dict) -> str:
    text: str = ""
    for child in elem["children"]:
        if child["type"] == "text":
            text += child["text"]
        if child["type"] == "codespan":
            text += f" `{child['text']}` "
    # A non-breaking space (``##\u00a0REQ-...`` or ``REQ-X:\u00a0Title``) would otherwise glue
    # the ID to the title.
    return text.replace("\u00a0", " ").strip()


def extract_title(parsed_content: list[dict]) -> str:
    # Extract the first L1 heading as the title. If no such heading exists, that's an error.
    if len(parsed_content) == 0:
        raise ValueError("File has no content")
    if is_ast_element_heading(parsed_content[0]) != 1:
        raise ValueError("First element in file is not L1 heading")
    return get_heading_text(parsed_content[0])


def _flatten_inline_text(node: dict) -> str:
    """Walk an inline AST node and join all text/codespan children, ignoring formatting."""
    if node.get("type") == "text":
        return node.get("text", "")
    if node.get("type") == "codespan":
        return node.get("text", "")
    if node.get("type") in {"softbreak", "linebreak"}:
        return "\n"
    text = ""
    for child in node.get("children", []):
        text += _flatten_inline_text(child)
    return text


def _split_metadata_bullet(list_item: dict) -> tuple[str, str] | None:
    """Return ``(label, value)`` for a ``- Label: value`` bullet, else ``None``.

    Recognised label-bearing children are the ``block_text`` direct child of a ``list_item``
    whose first text child looks like ``"Label: ..."``.
    """
    children = list_item.get("children", [])
    if not children:
        return None
    block_text = children[0]
    if block_text.get("type") != "block_text":
        return None
    inline_children = block_text.get("children", [])
    flat = "".join(_flatten_inline_text(child) for child in inline_children).strip()
    if ":" not in flat:
        return None
    label, _, value = flat.partition(":")
    label = label.strip()
    if not label or len(label) > 30:
        return None
    return label, value.strip()


def extract_autotest_result(parsed_content: list[dict], *, test_id: str) -> AutotestResult:
    """Pick out the Description / Assertions blocks from a parsed ``result.md`` AST.

    Returns the description text, the AST items that make up the ``## Assertions`` section,
    the original metadata bullet list (with Description removed), and a list of fields the
    generator should warn about (``"description"``, ``"assertions"``).
    """
    description = ""
    assertions_items: list[dict] = []
    other_items: list[dict] = []
    metadata_items: list[dict] = []

    consume_assertions = False
    for elem in parsed_content:
        if is_ast_element_heading(elem) == 1:
            # Skip the H1 (test ID heading) — caller renders it separately.
            consume_assertions = False
            continue
        if elem.get("type") == "heading" and elem.get("level") == 2:
            heading_text = get_heading_text(elem).strip().lower().rstrip(":")
            if heading_text == "assertions":
                consume_assertions = True
                continue
            consume_assertions = False
            other_items.append(elem)
            continue
        if consume_assertions:
            assertions_items.append(elem)
            continue
        if elem.get("type") == "list" and not metadata_items:
            new_children: list[dict] = []
            for list_item in elem.get("children", []):
                bullet = _split_metadata_bullet(list_item)
                if bullet and bullet[0].lower() == "description":
                    description = bullet[1]
                    continue
                new_children.append(list_item)
            if new_children:
                surviving_list = dict(elem)
                surviving_list["children"] = new_children
                metadata_items.append(surviving_list)
            continue
        other_items.append(elem)

    missing_fields: list[str] = []
    if not description:
        missing_fields.append("description")
    if not assertions_items:
        missing_fields.append("assertions")

    return AutotestResult(
        test_id=test_id,
        description=description,
        assertions_items=assertions_items,
        metadata_items=metadata_items,
        other_items=other_items,
        missing_fields=missing_fields,
    )
