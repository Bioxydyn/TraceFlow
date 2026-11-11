"""Parser and model: walk order, item headings, hierarchy, risk links and evidence bindings."""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from traceflow.parser import (
    HierarchyConfig,
    Requirement,
    extract_parent_ids,
    ordered_markdown_files,
    parse_markdown,
    process_directory,
)


def _write(path: Path, content: str, extra: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n" + extra)


def _write_config(root: Path, content: str) -> None:
    _write(root / "config.yml", content)


def _two_level_corpus(root: Path, *, extra_tier2: str = "", extra_tests: str = "") -> None:
    """Two Tier 1s, three Tier 2s, journey and functional shells, and a risk register."""
    _write(
        root / "requirements" / "Tier 1 Requirements.md",
        """
        # Tier 1

        ## REQ-T1-001: Ingest

        Ingest data.

        ## REQ-T1-002: Audit

        Audit everything.
        """,
    )
    _write(
        root / "requirements" / "Upload Requirements.md",
        """
        # Upload

        ## REQ-UP-001: Upload a file

        Parent: REQ-T1-001

        Upload one file.

        ## REQ-UP-002: Resume an upload

        Parent: REQ-T1-001

        Parent: REQ-T1-001

        Resume.

        ## REQ-AU-001: Record the upload

        **Parent:** REQ-T1-002

        Record it.
        """,
        extra_tier2,
    )
    _write(
        root / "tests" / "Upload Tests.md",
        """
        # Upload tests

        ## TEST-UP-001: Upload a file

        **Requirement ID:** REQ-UP-001: Upload a file

        ```autotest
        PY-UP-001
        ```

        ```autotest
        PW-SHARED
        ```

        ## TEST-UP-002: Resume

        **Requirement ID:** REQ-UP-002

        ```autotest
        PY-UP-002
        ```

        ```autotest
        PY-UP-002
        ```

        ## TEST-AU-001: Record

        **Requirement ID:** REQ-AU-001

        ```autotest
        PW-SHARED
        ```

        ## TEST-T1-001: Journey

        **Requirement ID:** REQ-T1-001

        ```autotest
        PW-SHARED
        ```
        """,
        extra_tests,
    )
    _write(
        root / "risks" / "risk-register.md",
        """
        # Risk Register

        ## RISK-001: Lost upload

        Controls: REQ-UP-001 verified by TEST-UP-002

        ## RISK-002: Missing record

        Controls: REQ-AU-001
        """,
    )


# ---------------------------------------------------------------- walk order


def test_walk_is_sorted_case_insensitively(tmp_path: Path) -> None:
    for name in ["b.md", "A.md", "c.md", "sub/z.md", "Sub2/a.md", "notes.txt"]:
        _write(tmp_path / name, "# T\n")
    assert ordered_markdown_files(str(tmp_path)) == ["A.md", "b.md", "c.md", "sub/z.md", "Sub2/a.md"]


def test_order_list_moves_matching_files_first(tmp_path: Path) -> None:
    for name in ["requirements/Audit.md", "requirements/Tier 1.md", "requirements/Upload.md"]:
        _write(tmp_path / name, "# T\n")
    order = ["requirements/Tier 1.md", "Upload.md"]
    assert ordered_markdown_files(str(tmp_path), order) == [
        "requirements/Tier 1.md",
        "requirements/Upload.md",
        "requirements/Audit.md",
    ]


def test_order_list_accepts_globs_and_rejects_stale_entries(tmp_path: Path) -> None:
    for name in ["tests/A Tests.md", "tests/Tier 1 Tests.md"]:
        _write(tmp_path / name, "# T\n")
    assert ordered_markdown_files(str(tmp_path), ["*Tier 1*"])[0] == "tests/Tier 1 Tests.md"
    with pytest.raises(ValueError, match="match no Markdown file"):
        ordered_markdown_files(str(tmp_path), ["tests/Gone.md"])


def test_process_directory_honours_config_order(tmp_path: Path) -> None:
    _two_level_corpus(tmp_path)
    _write(tmp_path / "requirements" / "Audit Requirements.md", "# Audit\n\n## REQ-ZZ-001: Z\n\nParent: REQ-T1-002\n")
    document = process_directory(str(tmp_path), version="1")
    assert [doc.title for doc in document.requirements] == ["Audit", "Tier 1", "Upload"]

    _write_config(tmp_path, "order:\n  - requirements/Tier 1 Requirements.md\n")
    document = process_directory(str(tmp_path), version="1")
    assert [doc.title for doc in document.requirements] == ["Tier 1", "Audit", "Upload"]


# ---------------------------------------------------------------- item headings


def test_non_id_headings_stay_prose(tmp_path: Path) -> None:
    _write(
        tmp_path / "tests" / "Coverage Gaps.md",
        """
        # Coverage Gaps

        Intro.

        ## Automated today

        Everything is automated.

        ## Allowlisted gaps (machine-readable)

        - `REQ-X` -- procedural
        """,
    )
    document = process_directory(str(tmp_path), version="1")
    gaps = document.tests[0]
    assert gaps.items == []
    headings = [elem for elem in gaps.generic_content if elem["type"] == "heading"]
    assert [elem["children"][0]["text"] for elem in headings] == ["Automated today", "Allowlisted gaps (machine-readable)"]


def test_prose_heading_after_an_item_ends_the_item(tmp_path: Path) -> None:
    _write(
        tmp_path / "requirements" / "reqs.md",
        """
        # Reqs

        ## REQ-001: One

        Body one.

        ## Notes

        A note.

        ## REQ-002: Two

        Body two.
        """,
    )
    document = process_directory(str(tmp_path), version="1")
    doc = document.requirements[0]
    assert [item.req_id for item in doc.items] == ["REQ-001", "REQ-002"]
    assert len(doc.items[0].content) == 1
    assert [elem["type"] for elem in doc.generic_content] == ["heading", "paragraph"]


def test_heading_only_items_are_kept(tmp_path: Path) -> None:
    _write(tmp_path / "requirements" / "reqs.md", "# Reqs\n\n## REQ-001: One\n\n## REQ-002: Two\n\nBody.\n")
    document = process_directory(str(tmp_path), version="1")
    assert [item.req_id for item in document.requirements[0].items] == ["REQ-001", "REQ-002"]


def test_non_breaking_space_after_hashes_and_colon(tmp_path: Path) -> None:
    _write(tmp_path / "requirements" / "reqs.md", "# Reqs\n\n## REQ-001: One\n\nBody.\n")
    document = process_directory(str(tmp_path), version="1")
    item = document.requirements[0].items[0]
    assert (item.req_id, item.title) == ("REQ-001", "One")


@pytest.mark.parametrize("heading", ["REQ_001: Underscored", "Req-001: Lower case", "TEST-001/002: Two IDs"])
def test_near_miss_item_headings_fail(tmp_path: Path, heading: str) -> None:
    _write(tmp_path / "tests" / "tests.md", f"# Tests\n\n## {heading}\n\nBody.\n")
    with pytest.raises(ValueError, match="looks like an item ID"):
        process_directory(str(tmp_path), version="1")


def test_prose_heading_that_starts_like_a_prefix_is_not_a_near_miss(tmp_path: Path) -> None:
    _write(tmp_path / "tests" / "tests.md", "# Tests\n\n## Test-driven notes\n\nBody.\n")
    document = process_directory(str(tmp_path), version="1")
    assert document.tests[0].items == []


def test_item_id_pattern_is_configurable(tmp_path: Path) -> None:
    _write(tmp_path / "tests" / "tests.md", "# Tests\n\n## UAT-001: Try it\n\nBody.\n")
    assert process_directory(str(tmp_path), version="1").tests[0].items == []
    _write_config(tmp_path, "item_id_pattern: '^(REQ|TEST|RISK|UAT)-[A-Z0-9-]+'\n")
    assert [t.test_id for t in process_directory(str(tmp_path), version="1").tests[0].items] == ["UAT-001"]


def test_invalid_item_id_pattern_is_reported(tmp_path: Path) -> None:
    _write_config(tmp_path, "item_id_pattern: '('\n")
    with pytest.raises(ValueError, match="not a valid regular expression"):
        process_directory(str(tmp_path), version="1")


# ---------------------------------------------------------------- parents and children


def test_parent_lines_are_parsed_deduplicated_and_linked(tmp_path: Path) -> None:
    _two_level_corpus(tmp_path)
    document = process_directory(str(tmp_path), version="1")
    index = document.requirements_by_id()
    assert index["REQ-UP-001"].parent_ids == ["REQ-T1-001"]
    assert index["REQ-UP-002"].parent_ids == ["REQ-T1-001"]
    assert index["REQ-AU-001"].parent_ids == ["REQ-T1-002"]
    assert index["REQ-T1-001"].child_ids == ["REQ-UP-001", "REQ-UP-002"]
    assert index["REQ-T1-002"].child_ids == ["REQ-AU-001"]
    # The Parent lines stay in the content (default rendering unchanged); body_content drops them.
    assert len(index["REQ-UP-002"].content) == 3
    assert len(index["REQ-UP-002"].body_content()) == 1


def test_extract_parent_ids_handles_lists_and_malformed_lines() -> None:
    content = parse_markdown("Parents: REQ-A, REQ-B (both)\n\nParent: to be decided\n\nText Parent: REQ-C\n")
    parent_ids, malformed = extract_parent_ids(content)
    assert parent_ids == ["REQ-A", "REQ-B"]
    assert malformed == ["Parent: to be decided"]


def test_body_content_keeps_mixed_paragraphs() -> None:
    content = parse_markdown("Parent: REQ-A\nand more text\n")
    requirement = Requirement(req_id="REQ-X", content=content, title="", test_ids=[])
    assert requirement.body_content() == content


# ---------------------------------------------------------------- verify_hierarchy


def _hierarchy_config(**overrides: object) -> str:
    settings = {"top_level_prefix": "REQ-T1-", "require_single_parent": "true"}
    settings.update({key: str(value) for key, value in overrides.items()})
    return "hierarchy:\n" + "".join(f"  {key}: {value}\n" for key, value in settings.items())


def test_hierarchy_is_unchecked_without_config(tmp_path: Path) -> None:
    _two_level_corpus(tmp_path, extra_tier2="\n## REQ-UP-009: Orphan\n\nParent: REQ-NOPE-001\n")
    document = process_directory(str(tmp_path), version="1")
    assert document.hierarchy is None
    assert document.requirements_by_id()["REQ-UP-009"].parent_ids == ["REQ-NOPE-001"]


def test_valid_hierarchy_passes(tmp_path: Path) -> None:
    _two_level_corpus(tmp_path)
    _write_config(tmp_path, _hierarchy_config())
    document = process_directory(str(tmp_path), version="1")
    assert document.hierarchy == HierarchyConfig(top_level_prefix="REQ-T1-", require_single_parent=True)
    assert [r.req_id for r in document.tier1_requirements()] == ["REQ-T1-001", "REQ-T1-002"]
    assert document.tier1_ids_of("REQ-UP-002") == ["REQ-T1-001"]
    assert document.tier1_ids_of("REQ-T1-002") == ["REQ-T1-002"]


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ("\n## REQ-UP-009: Orphan\n\nParent: REQ-NOPE-001\n", "names parent `REQ-NOPE-001`, which is not a requirement"),
        ("\n## REQ-UP-009: Self\n\nParent: REQ-UP-009\n", "names itself as its parent"),
        ("\n## REQ-UP-009: None\n\nNo parent here.\n", "must have exactly one Tier 1 parent"),
        ("\n## REQ-UP-009: Two\n\nParent: REQ-T1-001, REQ-T1-002\n", "must have exactly one Tier 1 parent"),
        ("\n## REQ-UP-009: Tier 2 parent\n\nParent: REQ-UP-001\n", "must have exactly one Tier 1 parent"),
        ("\n## REQ-UP-009: Typo\n\nParent: see above\n", "Parent line that names no ID"),
    ],
)
def test_hierarchy_errors_fail_the_parse(tmp_path: Path, extra: str, message: str) -> None:
    _two_level_corpus(tmp_path, extra_tier2=extra)
    _write_config(tmp_path, _hierarchy_config())
    with pytest.raises(ValueError, match=message):
        process_directory(str(tmp_path), version="1")


def test_parent_cycles_are_reported_once(tmp_path: Path) -> None:
    _write(
        tmp_path / "requirements" / "reqs.md",
        """
        # Reqs

        ## REQ-A: A

        Parent: REQ-B

        ## REQ-B: B

        Parent: REQ-C

        ## REQ-C: C

        Parent: REQ-A
        """,
    )
    _write_config(tmp_path, "hierarchy: {}\n")
    with pytest.raises(ValueError, match="Parent cycle: REQ-A -> REQ-B -> REQ-C -> REQ-A") as raised:
        process_directory(str(tmp_path), version="1")
    assert str(raised.value).count("Parent cycle") == 1


def test_tier1_with_a_parent_fails(tmp_path: Path) -> None:
    _two_level_corpus(tmp_path)
    tier1 = tmp_path / "requirements" / "Tier 1 Requirements.md"
    tier1.write_text(tier1.read_text().replace("Audit everything.", "Parent: REQ-T1-001\n\nAudit everything."))
    _write_config(tmp_path, _hierarchy_config())
    with pytest.raises(ValueError, match="Tier 1 requirement `REQ-T1-002` must not name a parent"):
        process_directory(str(tmp_path), version="1")


def test_without_single_parent_a_tier2_may_trace_through_another_tier2(tmp_path: Path) -> None:
    _two_level_corpus(tmp_path, extra_tier2="\n## REQ-UP-009: Deeper\n\nParent: REQ-UP-001\n")
    _write_config(tmp_path, "hierarchy:\n  top_level_prefix: REQ-T1-\n")
    document = process_directory(str(tmp_path), version="1")
    assert document.tier1_ids_of("REQ-UP-009") == ["REQ-T1-001"]


def test_procedural_files_are_exempt_but_must_stay_parentless(tmp_path: Path) -> None:
    _two_level_corpus(tmp_path)
    _write(tmp_path / "requirements" / "Procedural Controls.md", "# Procedural\n\n## REQ-PROC-001: Letter\n\nSend it.\n")
    _write_config(tmp_path, _hierarchy_config(procedural_files="['Procedural Controls.md']"))
    document = process_directory(str(tmp_path), version="1")
    assert document.procedural_requirement_ids() == ["REQ-PROC-001"]

    _write(
        tmp_path / "requirements" / "Procedural Controls.md",
        "# Procedural\n\n## REQ-PROC-001: Letter\n\nParent: REQ-T1-001\n",
    )
    with pytest.raises(ValueError, match="procedural file and must not name a parent"):
        process_directory(str(tmp_path), version="1")


def test_hierarchy_config_rejects_unknown_keys(tmp_path: Path) -> None:
    _write_config(tmp_path, "hierarchy:\n  top_level_prefx: REQ-T1-\n")
    with pytest.raises(ValueError, match="unknown keys"):
        process_directory(str(tmp_path), version="1")


# ---------------------------------------------------------------- risks and evidence


def test_requirements_link_to_risks_directly_and_through_shells(tmp_path: Path) -> None:
    _two_level_corpus(tmp_path)
    document = process_directory(str(tmp_path), version="1")
    index = document.requirements_by_id()
    assert index["REQ-UP-001"].risk_ids == ["RISK-001"]
    assert index["REQ-UP-002"].risk_ids == ["RISK-001"]  # via TEST-UP-002
    assert index["REQ-AU-001"].risk_ids == ["RISK-002"]
    assert index["REQ-T1-001"].risk_ids == []
    assert document.tests_by_id()["TEST-UP-002"].risk_ids == ["RISK-001"]


def test_evidence_ids_and_bindings(tmp_path: Path) -> None:
    _two_level_corpus(tmp_path)
    _write_config(tmp_path, _hierarchy_config())
    document = process_directory(str(tmp_path), version="1")
    tests = document.tests_by_id()
    assert tests["TEST-UP-001"].evidence_ids == ["PY-UP-001", "PW-SHARED"]
    assert tests["TEST-UP-002"].evidence_ids == ["PY-UP-002"]

    bindings = document.evidence_bindings()
    assert list(bindings) == ["PY-UP-001", "PW-SHARED", "PY-UP-002"]
    shared = bindings["PW-SHARED"]
    assert shared.shell_ids == ["TEST-UP-001", "TEST-AU-001", "TEST-T1-001"]
    assert shared.requirement_ids == ["REQ-UP-001", "REQ-AU-001", "REQ-T1-001"]
    assert shared.tier1_ids == ["REQ-T1-001", "REQ-T1-002"]
    assert bindings["PY-UP-002"].tier1_ids == ["REQ-T1-001"]


def test_evidence_bindings_without_hierarchy_have_no_tier1(tmp_path: Path) -> None:
    _two_level_corpus(tmp_path)
    bindings = process_directory(str(tmp_path), version="1").evidence_bindings()
    assert bindings["PW-SHARED"].tier1_ids == []
    explicit = process_directory(str(tmp_path), version="1").evidence_bindings(HierarchyConfig(top_level_prefix="REQ-T1-"))
    assert explicit["PW-SHARED"].tier1_ids == ["REQ-T1-001", "REQ-T1-002"]


def test_nested_autotest_blocks_are_not_bound(tmp_path: Path) -> None:
    _write(tmp_path / "requirements" / "r.md", "# R\n\n## REQ-1: R\n\nBody.\n")
    _write(
        tmp_path / "tests" / "t.md",
        "# T\n\n## TEST-1: T\n\n**Requirement ID:** REQ-1\n\n```autotest   \nPY-1\n```\n\n```text\nPY-2\n```\n",
    )
    document = process_directory(str(tmp_path), version="1")
    assert document.tests_by_id()["TEST-1"].evidence_ids == ["PY-1"]
