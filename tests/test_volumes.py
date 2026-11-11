"""Evidence-volume manifest, partition, home rule and the --check-volumes CLI mode."""
from __future__ import annotations

import json
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

import traceflow.__main__ as traceflow_main
from traceflow.parser import process_directory
from traceflow.volumes import (
    DEFAULT_REGULATORY_FAMILIES,
    ManifestError,
    VolumePartitionError,
    check_volumes,
    load_manifest,
    parse_manifest,
    plan_volumes,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _write(path: Path, content: str, extra: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(textwrap.dedent(content).strip() + "\n" + textwrap.dedent(extra))


def _corpus(root: Path, *, extra_requirements: str = "", extra_tests: str = "") -> Path:
    """Three Tier 1s; upload (T1-001), audit and a 21 CFR 11 clause (T1-002), reporting (T1-003)."""
    _write(
        root / "requirements" / "Tier 1 Requirements.md",
        """
        # Tier 1

        ## REQ-VF-T1-001: Ingest

        Ingest.

        ## REQ-VF-T1-002: Audit

        Audit.

        ## REQ-VF-T1-003: Report

        Report.
        """,
    )
    _write(
        root / "requirements" / "Family Requirements.md",
        """
        # Families

        ## REQ-VOX-UPLOAD-001: Upload

        Parent: REQ-VF-T1-001

        ## REQ-VOX-AUDITLOG-001: Audit entry

        Parent: REQ-VF-T1-002

        ## REQ-VOX-21CFR11-001: 11.10(e) audit trail

        Parent: REQ-VF-T1-002

        ## REQ-VOX-REPORT-001: Report

        Parent: REQ-VF-T1-003
        """,
        extra_requirements,
    )

    def shell(test_id: str, req_ids: list[str], evidence: list[str]) -> str:
        lines = [f"## {test_id}: Shell {test_id}", ""]
        lines += [f"**Requirement ID:** {req_id}" for req_id in req_ids] + [""]
        for evidence_id in evidence:
            lines += ["```autotest", evidence_id, "```", ""]
        return "\n".join(lines)

    shells = [
        shell("TEST-VOX-UPLOAD-001", ["REQ-VOX-UPLOAD-001"], ["PY-UPLOAD", "PW-SHARED-X"]),
        shell("TEST-VOX-21CFR11-001", ["REQ-VOX-21CFR11-001"], ["PY-AUDIT-CLAUSE", "PY-CLAUSE-ONLY"]),
        shell("TEST-VOX-AUDITLOG-001", ["REQ-VOX-AUDITLOG-001"], ["PY-AUDIT-CLAUSE", "PW-SHARED-X"]),
        shell("TEST-VOX-REPORT-001", ["REQ-VOX-REPORT-001"], ["PY-REPORT"]),
        shell("TEST-VF-T1-001", ["REQ-VF-T1-001"], ["PY-UPLOAD", "PW-JOURNEY"]),
        shell("TEST-VF-T1-002", ["REQ-VF-T1-002"], ["PY-CLAUSE-ONLY"]),
    ]
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "Family Tests.md").write_text("# Tests\n\n" + "\n".join(shells) + textwrap.dedent(extra_tests))
    return root


def _manifest(**overrides: Any) -> dict:
    manifest: dict = {
        "structure_revision": 1,
        "report": {"code": "BXD-VF-019", "title": "Traceability Report"},
        "tier1_prefix": "REQ-VF-T1-",
        "require_single_parent": True,
        "volumes": [
            {
                "code": "BXD-VF-019-E01",
                "slug": "ingestion",
                "title": "Ingestion",
                "audience": "Data managers",
                "tier1": ["REQ-VF-T1-001"],
                "max_pages": 150,
                "max_mb": 10,
            },
            {
                "code": "BXD-VF-019-E02",
                "slug": "audit",
                "title": "Audit and reporting",
                "tier1": ["REQ-VF-T1-002", "REQ-VF-T1-003"],
            },
        ],
    }
    manifest.update(overrides)
    return manifest


def _plan(root: Path, manifest: dict):  # noqa: ANN202 - VolumePlan, kept short for the tests
    return plan_volumes(process_directory(str(root), version="1"), parse_manifest(manifest))


# ---------------------------------------------------------------- manifest


def test_manifest_fields() -> None:
    manifest = parse_manifest(_manifest())
    assert (manifest.report_code, manifest.report_title, manifest.structure_revision) == (
        "BXD-VF-019",
        "Traceability Report",
        1,
    )
    first, second = manifest.volumes
    assert (first.code, first.slug, first.index, first.max_pages, first.max_mb) == (
        "BXD-VF-019-E01",
        "ingestion",
        1,
        150,
        10.0,
    )
    assert second.audience == "" and second.max_pages is None and second.index == 2
    assert manifest.regulatory_families == DEFAULT_REGULATORY_FAMILIES
    assert manifest.volume("audit") is second
    assert manifest.volume("BXD-VF-019-E01") is first
    assert manifest.tier1_order() == ["REQ-VF-T1-001", "REQ-VF-T1-002", "REQ-VF-T1-003"]
    with pytest.raises(KeyError):
        manifest.volume("E09")


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"volumes": []}, "`volumes` must be a non-empty list"),
        ({"report": {"code": "BXD-VF-019"}}, "`report.title` must be a non-empty string"),
        ({"colour": "blue"}, "unknown top-level keys"),
        ({"volumes": [{"code": "E1", "slug": "a", "title": "A", "tier1": ["REQ-VF-T1-001"], "teir1": []}]},
         "unknown keys"),
        ({"volumes": [{"code": "E1", "slug": "Bad Slug", "title": "A", "tier1": ["REQ-VF-T1-001"]}]},
         "must be lower-case"),
        ({"volumes": [{"code": "E1", "slug": "a", "title": "A", "tier1": []}]}, "lists no Tier 1"),
        ({"volumes": [{"code": "E1", "slug": "a", "title": "A", "tier1": ["REQ-VF-T1-001"], "max_pages": 0}]},
         "max_pages must be a positive integer"),
        ({"volumes": [{"code": "E1", "slug": "a", "title": "A", "tier1": ["REQ-VF-T1-001"]},
                      {"code": "E1", "slug": "b", "title": "B", "tier1": ["REQ-VF-T1-002"]}]},
         "volume code `E1` is used twice"),
        ({"volumes": [{"code": "BXD-VF-019", "slug": "a", "title": "A", "tier1": ["REQ-VF-T1-001"]}]},
         "the report's own code"),
        ({"volumes": [{"code": "E1", "slug": "a", "title": "A", "tier1": ["REQ-VF-T1-001"], "homes": [1]}]},
         "homes must map evidence IDs"),
    ],
)
def test_manifest_errors(change: dict, message: str) -> None:
    with pytest.raises(ManifestError, match=message):
        parse_manifest(_manifest(**change))


def test_manifest_reports_every_problem_at_once() -> None:
    with pytest.raises(ManifestError) as raised:
        parse_manifest({"report": {}, "volumes": [{"code": "", "slug": "", "title": "", "tier1": []}]})
    assert isinstance(raised.value, ManifestError)
    assert len(raised.value.errors) >= 4


def test_load_manifest_reads_yaml(tmp_path: Path) -> None:
    path = tmp_path / "volumes.yml"
    path.write_text(yaml.safe_dump(_manifest()))
    assert load_manifest(str(path)).path == str(path)
    path.write_text("volumes: [")
    with pytest.raises(ManifestError, match="not valid YAML"):
        load_manifest(str(path))


def test_voxelflow_ten_tier1_fixture_is_a_valid_manifest() -> None:
    manifest = load_manifest(str(FIXTURES / "voxelflow-volumes-10-tier1.yml"))
    assert [spec.code for spec in manifest.volumes] == [f"BXD-VF-019-E0{n}" for n in range(1, 7)]
    assert sorted(manifest.tier1_order()) == [f"REQ-VF-T1-{n:03d}" for n in range(1, 11)]


# ---------------------------------------------------------------- partition


def test_requirements_and_shells_follow_their_tier1(tmp_path: Path) -> None:
    plan = _plan(_corpus(tmp_path), _manifest())
    assert plan.tier1_volume == {
        "REQ-VF-T1-001": "BXD-VF-019-E01",
        "REQ-VF-T1-002": "BXD-VF-019-E02",
        "REQ-VF-T1-003": "BXD-VF-019-E02",
    }
    assert plan.requirement_volume["REQ-VOX-UPLOAD-001"] == "BXD-VF-019-E01"
    assert plan.requirement_volume["REQ-VOX-21CFR11-001"] == "BXD-VF-019-E02"
    # Corpus order: "Family Requirements.md" sorts before "Tier 1 Requirements.md".
    assert plan.requirement_ids("ingestion") == ["REQ-VOX-UPLOAD-001", "REQ-VF-T1-001"]
    classes = {shell_id: (shell.volume_code, shell.tier1_id, shell.shell_class) for shell_id, shell in plan.shells.items()}
    assert classes == {
        "TEST-VOX-UPLOAD-001": ("BXD-VF-019-E01", "REQ-VF-T1-001", "functional"),
        "TEST-VOX-21CFR11-001": ("BXD-VF-019-E02", "REQ-VF-T1-002", "regulatory"),
        "TEST-VOX-AUDITLOG-001": ("BXD-VF-019-E02", "REQ-VF-T1-002", "functional"),
        "TEST-VOX-REPORT-001": ("BXD-VF-019-E02", "REQ-VF-T1-003", "functional"),
        # Tier 1 journey shells go to their own Tier 1.
        "TEST-VF-T1-001": ("BXD-VF-019-E01", "REQ-VF-T1-001", "tier1"),
        "TEST-VF-T1-002": ("BXD-VF-019-E02", "REQ-VF-T1-002", "tier1"),
    }


def test_home_rule_functional_then_regulatory_then_tier1_then_manifest_order(tmp_path: Path) -> None:
    plan = _plan(_corpus(tmp_path), _manifest())
    homes = {evidence_id: (home.shell_id, home.reason) for evidence_id, home in plan.evidence.items()}
    assert homes == {
        # Functional Tier 2 beats the Tier 1 journey shell.
        "PY-UPLOAD": ("TEST-VOX-UPLOAD-001", "functional"),
        # Two functional shells in different volumes: manifest order picks E01.
        "PW-SHARED-X": ("TEST-VOX-UPLOAD-001", "functional"),
        # Functional beats the regulatory clause shell, even though the clause shell comes first.
        "PY-AUDIT-CLAUSE": ("TEST-VOX-AUDITLOG-001", "functional"),
        # Regulatory clause shell beats the Tier 1 journey shell.
        "PY-CLAUSE-ONLY": ("TEST-VOX-21CFR11-001", "regulatory"),
        "PY-REPORT": ("TEST-VOX-REPORT-001", "functional"),
        "PW-JOURNEY": ("TEST-VF-T1-001", "tier1"),
    }
    shared = plan.home_of("PW-SHARED-X")
    assert shared.bound_volumes == ["BXD-VF-019-E01", "BXD-VF-019-E02"]
    assert shared.binding.tier1_ids == ["REQ-VF-T1-001", "REQ-VF-T1-002"]
    assert plan.volume_of_evidence("PY-CLAUSE-ONLY") == "BXD-VF-019-E02"


def test_every_record_has_exactly_one_home(tmp_path: Path) -> None:
    plan = _plan(_corpus(tmp_path), _manifest())
    homed = plan.home_evidence_ids("ingestion") + plan.home_evidence_ids("audit")
    assert sorted(homed) == sorted(plan.evidence)
    assert len(homed) == len(set(homed))


def test_layout_groups_by_tier1_with_journey_shells_first(tmp_path: Path) -> None:
    plan = _plan(_corpus(tmp_path), _manifest())
    layout = plan.layout("BXD-VF-019-E02")
    assert [(group.tier1_id, group.title) for group in layout] == [("REQ-VF-T1-002", "Audit"), ("REQ-VF-T1-003", "Report")]
    assert [shell.shell_id for shell in layout[0].shells] == [
        "TEST-VF-T1-002",
        "TEST-VOX-21CFR11-001",
        "TEST-VOX-AUDITLOG-001",
    ]
    audit = layout[0].shells[2]
    assert audit.evidence_ids == ["PY-AUDIT-CLAUSE", "PW-SHARED-X"]
    assert audit.home_evidence_ids == ["PY-AUDIT-CLAUSE"]
    assert audit.pointer_evidence_ids == ["PW-SHARED-X"]
    clause = layout[0].shells[1]
    assert clause.home_evidence_ids == ["PY-CLAUSE-ONLY"]
    assert clause.pointer_evidence_ids == ["PY-AUDIT-CLAUSE"]  # same volume, other shell
    # Only records homed in another volume count as the volume's pointers.
    assert plan.pointer_evidence_ids("audit") == ["PW-SHARED-X"]
    assert plan.home_evidence_ids("audit") == ["PY-CLAUSE-ONLY", "PY-AUDIT-CLAUSE", "PY-REPORT"]


def test_home_overrides_win(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["volumes"][1]["homes"] = {"PW-SHARED-X": "TEST-VOX-AUDITLOG-001", "PY-CLAUSE-ONLY": None}
    manifest["volumes"][0]["homes"] = ["PY-UPLOAD"]
    plan = _plan(_corpus(tmp_path), manifest)
    assert (plan.home_of("PW-SHARED-X").shell_id, plan.home_of("PW-SHARED-X").reason) == (
        "TEST-VOX-AUDITLOG-001",
        "override",
    )
    assert plan.home_of("PY-CLAUSE-ONLY").shell_id == "TEST-VOX-21CFR11-001"
    assert plan.home_of("PY-UPLOAD").shell_id == "TEST-VOX-UPLOAD-001"
    assert plan.pointer_evidence_ids("ingestion") == ["PW-SHARED-X"]


@pytest.mark.parametrize(
    ("homes_e01", "homes_e02", "message"),
    [
        ({"PY-NOPE": None}, {}, "which no shell binds"),
        ({"PY-UPLOAD": "TEST-VOX-NOPE"}, {}, "which is not a shell"),
        ({"PY-UPLOAD": "TEST-VOX-REPORT-001"}, {}, "which does not bind it"),
        ({}, {"PW-SHARED-X": "TEST-VOX-UPLOAD-001"}, "which is in `BXD-VF-019-E01`"),
        ({}, {"PY-UPLOAD": None}, "no shell in that volume binds it"),
        ({"PW-SHARED-X": None}, {"PW-SHARED-X": None}, "has a home override in"),
    ],
)
def test_bad_home_overrides_fail(tmp_path: Path, homes_e01: dict, homes_e02: dict, message: str) -> None:
    manifest = _manifest()
    manifest["volumes"][0]["homes"] = homes_e01
    manifest["volumes"][1]["homes"] = homes_e02
    with pytest.raises(VolumePartitionError, match=message):
        _plan(_corpus(tmp_path), manifest)


def test_unassigned_tier1_fails(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["volumes"][1]["tier1"] = ["REQ-VF-T1-002"]
    with pytest.raises(VolumePartitionError, match="`REQ-VF-T1-003` is not assigned to any volume") as raised:
        _plan(_corpus(tmp_path), manifest)
    # The orphaned shells' evidence is not reported again.
    assert isinstance(raised.value, VolumePartitionError)
    assert raised.value.errors == ["Tier 1 `REQ-VF-T1-003` is not assigned to any volume"]


def test_tier1_in_two_volumes_fails(tmp_path: Path) -> None:
    manifest = _manifest()
    manifest["volumes"][0]["tier1"] = ["REQ-VF-T1-001", "REQ-VF-T1-003"]
    with pytest.raises(VolumePartitionError, match="is in two volumes: `BXD-VF-019-E01` and `BXD-VF-019-E02`"):
        _plan(_corpus(tmp_path), manifest)


@pytest.mark.parametrize(
    ("tier1", "message"),
    [("REQ-VF-T1-404", "which is not a requirement"), ("REQ-VOX-UPLOAD-001", "which is not a Tier 1")],
)
def test_manifest_tier1_must_be_a_corpus_tier1(tmp_path: Path, tier1: str, message: str) -> None:
    manifest = _manifest()
    manifest["volumes"][0]["tier1"] = ["REQ-VF-T1-001", tier1]
    with pytest.raises(VolumePartitionError, match=message):
        _plan(_corpus(tmp_path), manifest)


def test_shell_spanning_two_volumes_fails(tmp_path: Path) -> None:
    extra = "\n## TEST-VOX-MIXED-001: Mixed\n\n**Requirement ID:** REQ-VOX-UPLOAD-001\n\n**Requirement ID:** REQ-VOX-REPORT-001\n"
    with pytest.raises(VolumePartitionError, match="shell `TEST-VOX-MIXED-001` verifies requirements in two volumes"):
        _plan(_corpus(tmp_path, extra_tests=extra), _manifest())


def test_requirement_tracing_to_two_volumes_fails(tmp_path: Path) -> None:
    extra = "\n## REQ-VOX-MULTI-001: Two parents\n\nParent: REQ-VF-T1-001, REQ-VF-T1-003\n"
    with pytest.raises(VolumePartitionError, match="requirement `REQ-VOX-MULTI-001` traces to Tier 1s in volumes"):
        _plan(_corpus(tmp_path, extra_requirements=extra), _manifest(require_single_parent=False))


def test_single_parent_rule_comes_from_the_manifest(tmp_path: Path) -> None:
    extra = "\n## REQ-VOX-MULTI-001: Two parents\n\nParent: REQ-VF-T1-002, REQ-VF-T1-003\n"
    plan = _plan(_corpus(tmp_path, extra_requirements=extra), _manifest(require_single_parent=False))
    assert plan.requirement_volume["REQ-VOX-MULTI-001"] == "BXD-VF-019-E02"
    with pytest.raises(VolumePartitionError, match="must have exactly one Tier 1 parent"):
        _plan(tmp_path, _manifest())


def test_procedural_requirements_have_no_volume(tmp_path: Path) -> None:
    _corpus(tmp_path)
    _write(tmp_path / "requirements" / "Procedural Controls.md", "# Procedural\n\n## REQ-VOX-PROC-001: Letter\n\nSend it.\n")
    manifest = _manifest(procedural_files=["Procedural Controls.md"])
    plan = _plan(tmp_path, manifest)
    assert plan.procedural_requirement_ids == ["REQ-VOX-PROC-001"]
    assert plan.requirement_volume["REQ-VOX-PROC-001"] is None

    with pytest.raises(VolumePartitionError, match="must have exactly one Tier 1 parent"):
        _plan(tmp_path, _manifest())


def test_evidence_bound_only_by_procedural_shells_fails(tmp_path: Path) -> None:
    _corpus(
        tmp_path,
        extra_tests="\n## TEST-VOX-PROC-001: Letter\n\n**Requirement ID:** REQ-VOX-PROC-001\n\n```autotest\nPY-PROC\n```\n",
    )
    _write(tmp_path / "requirements" / "Procedural Controls.md", "# Procedural\n\n## REQ-VOX-PROC-001: Letter\n\nSend it.\n")
    manifest = _manifest(procedural_files=["Procedural Controls.md"])
    with pytest.raises(VolumePartitionError, match="`PY-PROC` is bound only by shells outside every volume"):
        _plan(tmp_path, manifest)


def test_regulatory_families_can_be_turned_off(tmp_path: Path) -> None:
    plan = _plan(_corpus(tmp_path), _manifest(regulatory_families=[]))
    assert plan.shells["TEST-VOX-21CFR11-001"].shell_class == "functional"
    # Corpus order then decides between the two functional shells of the same Tier 1.
    assert plan.home_of("PY-AUDIT-CLAUSE").shell_id == "TEST-VOX-21CFR11-001"


def test_tier1_prefix_from_config_and_conflicts(tmp_path: Path) -> None:
    _corpus(tmp_path)
    manifest = _manifest()
    del manifest["tier1_prefix"]
    with pytest.raises(VolumePartitionError, match="no Tier 1 prefix"):
        _plan(tmp_path, manifest)
    (tmp_path / "config.yml").write_text("hierarchy:\n  top_level_prefix: REQ-VF-T1-\n")
    assert _plan(tmp_path, manifest).hierarchy.top_level_prefix == "REQ-VF-T1-"
    with pytest.raises(VolumePartitionError, match="differs from config.yml"):
        _plan(tmp_path, _manifest(tier1_prefix="REQ-T1-"))


def test_plan_to_dict_is_the_id_to_volume_map(tmp_path: Path) -> None:
    report = _plan(_corpus(tmp_path), _manifest()).to_dict()
    assert report["ok"] is True and report["errors"] == []
    assert report["report"] == {"code": "BXD-VF-019", "title": "Traceability Report"}
    assert [(v["code"], v["index"], v["of"], v["home_record_count"], v["pointer_record_count"]) for v in report["volumes"]] == [
        ("BXD-VF-019-E01", 1, 2, 3, 0),
        ("BXD-VF-019-E02", 2, 2, 3, 1),
    ]
    assert report["tier1"]["REQ-VF-T1-003"] == "BXD-VF-019-E02"
    assert report["requirements"]["REQ-VOX-UPLOAD-001"] == "BXD-VF-019-E01"
    assert report["shells"]["TEST-VF-T1-001"] == {
        "volume": "BXD-VF-019-E01",
        "tier1": "REQ-VF-T1-001",
        "class": "tier1",
        "requirements": ["REQ-VF-T1-001"],
    }
    assert report["evidence"]["PW-SHARED-X"] == {
        "volume": "BXD-VF-019-E01",
        "shell": "TEST-VOX-UPLOAD-001",
        "reason": "functional",
        "bound_volumes": ["BXD-VF-019-E01", "BXD-VF-019-E02"],
        "shells": ["TEST-VOX-UPLOAD-001", "TEST-VOX-AUDITLOG-001"],
        "requirements": ["REQ-VOX-UPLOAD-001", "REQ-VOX-AUDITLOG-001"],
        "tier1": ["REQ-VF-T1-001", "REQ-VF-T1-002"],
    }
    json.dumps(report)


# ---------------------------------------------------------------- check_volumes and the CLI


def _manifest_file(tmp_path: Path, manifest: dict) -> str:
    path = tmp_path / "volumes.yml"
    path.write_text(yaml.safe_dump(manifest))
    return str(path)


def test_check_volumes_reports_parse_and_partition_errors(tmp_path: Path) -> None:
    corpus = _corpus(tmp_path / "corpus")
    good = _manifest_file(tmp_path, _manifest())
    report, code = check_volumes(str(corpus), good)
    assert code == 0 and report["ok"]

    bad = _manifest()
    bad["volumes"][1]["tier1"] = ["REQ-VF-T1-002"]
    report, code = check_volumes(str(corpus), _manifest_file(tmp_path, bad))
    assert code == 1 and report["ok"] is False
    assert report["errors"] == ["Tier 1 `REQ-VF-T1-003` is not assigned to any volume"]

    (corpus / "tests" / "Dup Tests.md").write_text("# Dup\n\n## TEST-VF-T1-001: again\n\nx\n")
    report, code = check_volumes(str(corpus), good)
    assert code == 1 and "Duplicate IDs found" in report["errors"][0]

    report, code = check_volumes(str(corpus), str(tmp_path / "missing.yml"))
    assert code == 1 and report["errors"]


def test_cli_check_volumes_prints_only_json_on_stdout(tmp_path: Path, capsys: Any) -> None:
    corpus = _corpus(tmp_path / "corpus")
    # A shell whose Requirement ID line is empty makes the parser print a warning.
    with open(corpus / "tests" / "Family Tests.md", "a") as handle:
        handle.write("\n## TEST-VOX-EMPTY-001: Empty\n\n**Requirement ID:**\n")
    manifest = _manifest_file(tmp_path, _manifest())

    assert traceflow_main._run(str(corpus), check_volumes_manifest=manifest) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["evidence"]["PY-REPORT"]["volume"] == "BXD-VF-019-E02"
    assert "Warning, Requirement ID not found" in captured.err

    bad = _manifest()
    bad["volumes"][0]["tier1"] = ["REQ-VF-T1-001", "REQ-VF-T1-002"]
    assert traceflow_main._run(str(corpus), "1.0", check_volumes_manifest=_manifest_file(tmp_path, bad)) == 1
    captured = capsys.readouterr()
    assert json.loads(captured.out)["ok"] is False
    assert "ERROR: Tier 1 `REQ-VF-T1-002` is in two volumes" in captured.err


def test_cli_render_still_needs_version_and_output(tmp_path: Path, capsys: Any) -> None:
    assert traceflow_main._run(str(tmp_path)) == 2
    assert "needs DIRECTORY VERSION OUTPUT" in capsys.readouterr().err
