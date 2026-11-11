"""Partition a traceability corpus into evidence volumes.

The report (for example BXD-VF-019) is issued with evidence volumes (BXD-VF-019-E01, ...).
Each volume holds whole Tier 1s. A YAML manifest names the volumes and their Tier 1s; this
module assigns every requirement and test shell to a volume, and gives every evidence
record exactly one home: the volume and shell under which it is printed in full. Every
other binding of the record prints a pointer to that home.

The whole corpus is always parsed and checked (``process_directory``); a volume render
selects from this plan rather than rendering a subfolder.

Manifest (YAML)::

    structure_revision: 1
    report:
      code: BXD-VF-019
      title: Requirements, Tests and Risk Traceability Report
    tier1_prefix: REQ-VF-T1-          # optional when config.yml sets hierarchy.top_level_prefix
    require_single_parent: true       # optional, default false
    procedural_files: []              # optional globs for parentless requirement files
    regulatory_families:              # optional; these two are the default
      - REQ-VOX-21CFR11-*
      - REQ-VOX-ANNEX11-*
    volumes:
      - code: BXD-VF-019-E01
        slug: ingestion
        title: Data Ingestion, De-identification and Upload Portals
        audience: Sponsor data managers and site data-protection reviewers
        tier1: [REQ-VF-T1-001, REQ-VF-T1-002]
        max_pages: 150
        max_mb: 10
        homes:                        # optional home overrides
          PW-TEST-VOX-UPLOAD-016: TEST-VOX-UPLOAD-016   # evidence ID -> home shell
          PY-some-test: null          # homed in this volume; the rule picks the shell

Home rule, for a record bound from several shells: a functional Tier 2 shell first, then
a shell whose requirements are all in a regulatory family, then a Tier 1 journey shell.
Ties go to manifest order (volume, then Tier 1), then corpus order. Overrides win.
"""
from __future__ import annotations

import contextlib
import fnmatch
import json
import re
import sys
from dataclasses import dataclass, field
from typing import Optional, Union, cast

import yaml

from traceflow.parser import Document, EvidenceBinding, HierarchyConfig, process_directory

DEFAULT_REGULATORY_FAMILIES = ["REQ-VOX-21CFR11-*", "REQ-VOX-ANNEX11-*"]

SHELL_FUNCTIONAL = "functional"
SHELL_REGULATORY = "regulatory"
SHELL_TIER1 = "tier1"
SHELL_PROCEDURAL = "procedural"
SHELL_UNASSIGNED = "unassigned"
_HOME_RANK = {SHELL_FUNCTIONAL: 0, SHELL_REGULATORY: 1, SHELL_TIER1: 2}

_MANIFEST_KEYS = {
    "structure_revision",
    "report",
    "tier1_prefix",
    "require_single_parent",
    "procedural_files",
    "regulatory_families",
    "volumes",
}
_REPORT_KEYS = {"code", "title"}
_VOLUME_KEYS = {"code", "slug", "title", "audience", "tier1", "max_pages", "max_mb", "homes"}
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]*")


class ManifestError(ValueError):
    def __init__(self, path: str, errors: list[str]) -> None:
        super().__init__(path, errors)
        self.path = path
        self.errors = errors

    def __str__(self) -> str:
        return f"Volume manifest {self.path} is invalid:\n  - " + "\n  - ".join(self.errors)


class VolumePartitionError(ValueError):
    def __init__(self, errors: list[str]) -> None:
        super().__init__(errors)
        self.errors = errors

    def __str__(self) -> str:
        return "Volume partition check failed:\n  - " + "\n  - ".join(self.errors)


@dataclass
class VolumeSpec:
    code: str
    slug: str
    title: str
    tier1_ids: list[str]
    # 1-based position in the manifest ("Volume <index> of <count>").
    index: int
    audience: str = ""
    max_pages: Optional[int] = None
    max_mb: Optional[float] = None
    # Evidence ID -> home shell ID, or ``None`` to let the rule pick a shell in this volume.
    homes: dict[str, Optional[str]] = field(default_factory=dict)


@dataclass
class VolumeManifest:
    path: str
    report_code: str
    report_title: str
    volumes: list[VolumeSpec]
    structure_revision: Optional[Union[int, str]] = None
    tier1_prefix: str = ""
    require_single_parent: Optional[bool] = None
    procedural_files: list[str] = field(default_factory=list)
    regulatory_families: list[str] = field(default_factory=lambda: list(DEFAULT_REGULATORY_FAMILIES))

    def volume(self, code_or_slug: str) -> VolumeSpec:
        for spec in self.volumes:
            if code_or_slug in (spec.code, spec.slug):
                return spec
        known = ", ".join(spec.code for spec in self.volumes)
        raise KeyError(f"No volume `{code_or_slug}` in {self.path} (volumes: {known})")

    def tier1_order(self) -> list[str]:
        return [tier1_id for spec in self.volumes for tier1_id in spec.tier1_ids]


def _as_mapping(raw: object) -> Optional[dict[str, object]]:
    if not isinstance(raw, dict):
        return None
    return {str(key): value for key, value in cast("dict[object, object]", raw).items()}


def _string_list(value: object, what: str, errors: list[str]) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        errors.append(f"`{what}` must be a list of non-empty strings")
        return []
    items: list[str] = []
    for item in cast("list[object]", value):
        if not isinstance(item, str) or not item.strip():
            errors.append(f"`{what}` must be a list of non-empty strings")
            return []
        items.append(item.strip())
    return items


def _text(data: dict[str, object], key: str, what: str, errors: list[str], *, required: bool = True) -> str:
    value = data.get(key)
    if value is None and not required:
        return ""
    if not isinstance(value, str) or (required and not value.strip()):
        errors.append(f"`{what}` must be a {'non-empty ' if required else ''}string")
        return ""
    return value.strip()


def _positive_number(
    data: dict[str, object], key: str, what: str, errors: list[str], *, integer: bool
) -> Optional[float]:
    value = data.get(key)
    if value is None:
        return None
    allowed = (int,) if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, allowed) or value <= 0:
        errors.append(f"{what} {key} must be a positive {'integer' if integer else 'number'}")
        return None
    return float(value)


def _homes(raw: object, where: str, errors: list[str]) -> dict[str, Optional[str]]:
    """``homes:`` as ``{evidence ID: shell ID or None}``; a plain list means ``None`` for each."""
    if raw is None:
        return {}
    message = f"{where} homes must map evidence IDs to shell IDs (or null)"
    if isinstance(raw, list):
        items = cast("list[object]", raw)
        raw = dict.fromkeys(items) if all(isinstance(item, str) for item in items) else None
    if not isinstance(raw, dict):
        errors.append(message)
        return {}
    homes: dict[str, Optional[str]] = {}
    for evidence_id, shell_id in cast("dict[object, object]", raw).items():
        if not isinstance(evidence_id, str) or not evidence_id.strip():
            errors.append(message)
            continue
        if shell_id is not None and not isinstance(shell_id, str):
            errors.append(message)
            continue
        homes[evidence_id.strip()] = shell_id.strip() if isinstance(shell_id, str) else None
    return homes


def _volume_spec(raw: object, position: int, errors: list[str]) -> Optional[VolumeSpec]:
    where = f"volumes[{position - 1}]"
    data = _as_mapping(raw)
    if data is None:
        errors.append(f"`{where}` must be a mapping")
        return None
    unknown = sorted(set(data) - _VOLUME_KEYS)
    if unknown:
        errors.append(f"`{where}` has unknown keys {unknown} (known: {sorted(_VOLUME_KEYS)})")
    code = _text(data, "code", f"{where}.code", errors)
    slug = _text(data, "slug", f"{where}.slug", errors)
    title = _text(data, "title", f"{where}.title", errors)
    audience = _text(data, "audience", f"{where}.audience", errors, required=False)
    where = f"volume `{code or where}`"
    if slug and not _SLUG.fullmatch(slug):
        errors.append(f"{where} slug `{slug}` must be lower-case letters, digits and hyphens")
    tier1_ids = _string_list(data.get("tier1"), f"{where}.tier1", errors)
    if not tier1_ids:
        errors.append(f"{where} lists no Tier 1 requirements")
    max_pages = _positive_number(data, "max_pages", where, errors, integer=True)
    return VolumeSpec(
        code=code,
        slug=slug,
        title=title,
        audience=audience,
        tier1_ids=tier1_ids,
        index=position,
        max_pages=int(max_pages) if max_pages is not None else None,
        max_mb=_positive_number(data, "max_mb", where, errors, integer=False),
        homes=_homes(data.get("homes"), where, errors),
    )


def parse_manifest(raw: object, path: str = "<manifest>") -> VolumeManifest:
    """Validate a loaded manifest mapping. Every problem is reported at once."""
    errors: list[str] = []
    data = _as_mapping(raw)
    if data is None:
        raise ManifestError(path, ["the manifest must be a mapping"])
    unknown = sorted(set(data) - _MANIFEST_KEYS)
    if unknown:
        errors.append(f"unknown top-level keys {unknown} (known: {sorted(_MANIFEST_KEYS)})")

    report = _as_mapping(data.get("report"))
    report_code = report_title = ""
    if report is None:
        errors.append("`report` must be a mapping with `code` and `title`")
    else:
        extra = sorted(set(report) - _REPORT_KEYS)
        if extra:
            errors.append(f"`report` has unknown keys {extra}")
        report_code = _text(report, "code", "report.code", errors)
        report_title = _text(report, "title", "report.title", errors)

    revision = data.get("structure_revision")
    structure_revision: Optional[Union[int, str]] = None
    if isinstance(revision, (int, str)) and not isinstance(revision, bool):
        structure_revision = revision
    elif revision is not None:
        errors.append("`structure_revision` must be an integer or a string")

    prefix = _text(data, "tier1_prefix", "tier1_prefix", errors, required=False)
    single = data.get("require_single_parent")
    if single is not None and not isinstance(single, bool):
        errors.append("`require_single_parent` must be true or false")
        single = None
    procedural = _string_list(data.get("procedural_files"), "procedural_files", errors)
    families = (
        _string_list(data.get("regulatory_families"), "regulatory_families", errors)
        if "regulatory_families" in data
        else list(DEFAULT_REGULATORY_FAMILIES)
    )

    volumes: list[VolumeSpec] = []
    raw_volumes = data.get("volumes")
    if not isinstance(raw_volumes, list) or not raw_volumes:
        errors.append("`volumes` must be a non-empty list")
    else:
        for position, raw_volume in enumerate(cast("list[object]", raw_volumes), start=1):
            spec = _volume_spec(raw_volume, position, errors)
            if spec is not None:
                volumes.append(spec)
    for attribute in ("code", "slug"):
        seen: set[str] = set()
        for spec in volumes:
            value = getattr(spec, attribute)
            if value and value in seen:
                errors.append(f"volume {attribute} `{value}` is used twice")
            seen.add(value)
    if report_code and any(spec.code == report_code for spec in volumes):
        errors.append(f"volume code `{report_code}` is the report's own code")

    if errors:
        raise ManifestError(path, errors)
    return VolumeManifest(
        path=path,
        report_code=report_code,
        report_title=report_title,
        volumes=volumes,
        structure_revision=structure_revision,
        tier1_prefix=prefix,
        require_single_parent=single if isinstance(single, bool) else None,
        procedural_files=procedural,
        regulatory_families=families,
    )


def load_manifest(path: str) -> VolumeManifest:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = yaml.safe_load(handle)
    except yaml.YAMLError as exc:
        raise ManifestError(path, [f"not valid YAML: {exc}"]) from exc
    return parse_manifest(raw, path)


# ---------------------------------------------------------------------- plan


@dataclass
class ShellPlacement:
    shell_id: str
    title: str
    requirement_ids: list[str]
    # Tier 1s the shell's requirements trace to, in manifest order.
    tier1_ids: list[str]
    volume_code: Optional[str]
    # The Tier 1 the shell is grouped under in its volume (first of ``tier1_ids``).
    tier1_id: Optional[str]
    shell_class: str
    # Every evidence ID the shell binds, in binding order.
    evidence_ids: list[str] = field(default_factory=list)
    # Records printed in full under this shell.
    home_evidence_ids: list[str] = field(default_factory=list)
    # Records bound here whose full copy is under another shell (this or another volume).
    pointer_evidence_ids: list[str] = field(default_factory=list)


@dataclass
class EvidenceHome:
    evidence_id: str
    volume_code: str
    shell_id: str
    # "functional", "regulatory", "tier1" (the rule that chose the home) or "override".
    reason: str
    # Volumes with at least one shell binding the record, in manifest order.
    bound_volumes: list[str]
    binding: EvidenceBinding


@dataclass
class Tier1Group:
    tier1_id: str
    title: str
    # Journey shells (all requirements Tier 1) first, then the others in corpus order.
    shells: list[ShellPlacement]


@dataclass
class VolumePlan:
    manifest: VolumeManifest
    hierarchy: HierarchyConfig
    tier1_volume: dict[str, str]
    requirement_volume: dict[str, Optional[str]]
    shells: dict[str, ShellPlacement]
    evidence: dict[str, EvidenceHome]
    procedural_requirement_ids: list[str]
    tier1_titles: dict[str, str]

    def home_of(self, evidence_id: str) -> EvidenceHome:
        return self.evidence[evidence_id]

    def volume_of_evidence(self, evidence_id: str) -> str:
        return self.evidence[evidence_id].volume_code

    def layout(self, code_or_slug: str) -> list[Tier1Group]:
        """The volume body: its Tier 1s in manifest order, each with the shells under it."""
        spec = self.manifest.volume(code_or_slug)
        groups: list[Tier1Group] = []
        for tier1_id in spec.tier1_ids:
            shells = [shell for shell in self.shells.values() if shell.volume_code == spec.code
                      and shell.tier1_id == tier1_id]
            shells.sort(key=lambda shell: 0 if shell.shell_class == SHELL_TIER1 else 1)
            groups.append(Tier1Group(tier1_id=tier1_id, title=self.tier1_titles.get(tier1_id, ""), shells=shells))
        return groups

    def home_evidence_ids(self, code_or_slug: str) -> list[str]:
        """Records printed in full in the volume, in print order."""
        return [
            evidence_id
            for group in self.layout(code_or_slug)
            for shell in group.shells
            for evidence_id in shell.home_evidence_ids
        ]

    def pointer_evidence_ids(self, code_or_slug: str) -> list[str]:
        """Records bound in the volume but printed in full in another volume, once each."""
        code = self.manifest.volume(code_or_slug).code
        found: list[str] = []
        for group in self.layout(code):
            for shell in group.shells:
                for evidence_id in shell.pointer_evidence_ids:
                    if self.evidence[evidence_id].volume_code != code and evidence_id not in found:
                        found.append(evidence_id)
        return found

    def requirement_ids(self, code_or_slug: str) -> list[str]:
        code = self.manifest.volume(code_or_slug).code
        return [req_id for req_id, volume in self.requirement_volume.items() if volume == code]

    def to_dict(self) -> dict:
        """The ID-to-volume map printed by ``traceflow --check-volumes``."""
        volumes = []
        for spec in self.manifest.volumes:
            volumes.append(
                {
                    "code": spec.code,
                    "slug": spec.slug,
                    "title": spec.title,
                    "audience": spec.audience,
                    "index": spec.index,
                    "of": len(self.manifest.volumes),
                    "tier1": spec.tier1_ids,
                    "max_pages": spec.max_pages,
                    "max_mb": spec.max_mb,
                    "requirement_count": len(self.requirement_ids(spec.code)),
                    "shell_count": sum(1 for shell in self.shells.values() if shell.volume_code == spec.code),
                    "home_record_count": len(self.home_evidence_ids(spec.code)),
                    "pointer_record_count": len(self.pointer_evidence_ids(spec.code)),
                }
            )
        return {
            "ok": True,
            "manifest": self.manifest.path,
            "structure_revision": self.manifest.structure_revision,
            "report": {"code": self.manifest.report_code, "title": self.manifest.report_title},
            "tier1_prefix": self.hierarchy.top_level_prefix,
            "volumes": volumes,
            "tier1": dict(self.tier1_volume),
            "requirements": dict(self.requirement_volume),
            "procedural_requirements": list(self.procedural_requirement_ids),
            "shells": {
                shell.shell_id: {
                    "volume": shell.volume_code,
                    "tier1": shell.tier1_id,
                    "class": shell.shell_class,
                    "requirements": shell.requirement_ids,
                }
                for shell in self.shells.values()
            },
            "evidence": {
                home.evidence_id: {
                    "volume": home.volume_code,
                    "shell": home.shell_id,
                    "reason": home.reason,
                    "bound_volumes": home.bound_volumes,
                    "shells": home.binding.shell_ids,
                    "requirements": home.binding.requirement_ids,
                    "tier1": home.binding.tier1_ids,
                }
                for home in self.evidence.values()
            },
            "errors": [],
        }


def _effective_hierarchy(document: Document, manifest: VolumeManifest, errors: list[str]) -> HierarchyConfig:
    base = document.hierarchy if document.hierarchy is not None else HierarchyConfig()
    prefix = base.top_level_prefix
    if manifest.tier1_prefix:
        if prefix and prefix != manifest.tier1_prefix:
            errors.append(
                f"the manifest tier1_prefix `{manifest.tier1_prefix}` differs from config.yml "
                f"hierarchy.top_level_prefix `{prefix}`"
            )
        prefix = manifest.tier1_prefix
    if not prefix:
        errors.append(
            "no Tier 1 prefix: set `tier1_prefix` in the manifest or hierarchy.top_level_prefix in config.yml"
        )
    single = base.require_single_parent if manifest.require_single_parent is None else manifest.require_single_parent
    procedural = list(base.procedural_files)
    for pattern in manifest.procedural_files:
        if pattern not in procedural:
            procedural.append(pattern)
    return HierarchyConfig(top_level_prefix=prefix, require_single_parent=single, procedural_files=procedural)


def _unique(values: list[str]) -> list[str]:
    found: list[str] = []
    for value in values:
        if value not in found:
            found.append(value)
    return found


def plan_volumes(document: Document, manifest: VolumeManifest) -> VolumePlan:
    """Assign requirements, shells and evidence homes to volumes; fail on any partition error."""
    errors: list[str] = []
    hierarchy = _effective_hierarchy(document, manifest, errors)
    if not hierarchy.top_level_prefix:
        raise VolumePartitionError(errors)
    errors.extend(document.hierarchy_errors(hierarchy))

    requirements = document.requirements_by_id()
    tests = document.tests_by_id()
    corpus_tier1 = [requirement.req_id for requirement in document.tier1_requirements(hierarchy)]
    procedural = document.procedural_requirement_ids(hierarchy)
    procedural_set = set(procedural)

    # Tier 1 -> volume.
    tier1_volume: dict[str, str] = {}
    for spec in manifest.volumes:
        for tier1_id in spec.tier1_ids:
            if tier1_id not in requirements:
                errors.append(f"volume `{spec.code}` lists `{tier1_id}`, which is not a requirement")
                continue
            if not document.is_tier1(tier1_id, hierarchy):
                errors.append(
                    f"volume `{spec.code}` lists `{tier1_id}`, which is not a Tier 1 "
                    f"({hierarchy.top_level_prefix}*) requirement"
                )
                continue
            if tier1_id in tier1_volume:
                if tier1_volume[tier1_id] == spec.code:
                    errors.append(f"Tier 1 `{tier1_id}` is listed twice by volume `{spec.code}`")
                else:
                    errors.append(
                        f"Tier 1 `{tier1_id}` is in two volumes: `{tier1_volume[tier1_id]}` and `{spec.code}`"
                    )
                continue
            tier1_volume[tier1_id] = spec.code
    for tier1_id in corpus_tier1:
        if tier1_id not in tier1_volume:
            errors.append(f"Tier 1 `{tier1_id}` is not assigned to any volume")

    tier1_rank = {tier1_id: position for position, tier1_id in enumerate(manifest.tier1_order())}
    volume_rank = {spec.code: spec.index for spec in manifest.volumes}

    def by_manifest(tier1_ids: list[str]) -> list[str]:
        return sorted(_unique(tier1_ids), key=lambda tier1_id: tier1_rank.get(tier1_id, len(tier1_rank)))

    # Requirement -> volume.
    requirement_tier1: dict[str, list[str]] = {}
    requirement_volume: dict[str, Optional[str]] = {}
    for req_id in requirements:
        tier1_ids = by_manifest(document.tier1_ids_of(req_id, hierarchy))
        requirement_tier1[req_id] = tier1_ids
        volumes = _unique([tier1_volume[tier1_id] for tier1_id in tier1_ids if tier1_id in tier1_volume])
        if len(volumes) > 1:
            errors.append(f"requirement `{req_id}` traces to Tier 1s in volumes {', '.join(volumes)}")
        requirement_volume[req_id] = volumes[0] if volumes else None

    def is_regulatory(req_id: str) -> bool:
        return any(fnmatch.fnmatchcase(req_id, pattern) for pattern in manifest.regulatory_families)

    # Shell -> volume and class.
    shells: dict[str, ShellPlacement] = {}
    shell_rank: dict[str, int] = {}
    for position, (test_id, test) in enumerate(tests.items()):
        shell_rank[test_id] = position
        req_ids = _unique(test.req_ids)
        tier1_ids = by_manifest([tier1_id for req_id in req_ids for tier1_id in requirement_tier1.get(req_id, [])])
        volumes = _unique([requirement_volume[req_id] or "" for req_id in req_ids if requirement_volume.get(req_id)])
        if len(volumes) > 1:
            spans = "; ".join(
                f"{volume}: " + ", ".join(req_id for req_id in req_ids if requirement_volume.get(req_id) == volume)
                for volume in volumes
            )
            errors.append(f"shell `{test_id}` verifies requirements in two volumes ({spans}); split the shell")
        non_procedural = [req_id for req_id in req_ids if req_id not in procedural_set]
        tier2 = [req_id for req_id in non_procedural if not document.is_tier1(req_id, hierarchy)]
        if not req_ids:
            shell_class = SHELL_UNASSIGNED
        elif not non_procedural:
            shell_class = SHELL_PROCEDURAL
        elif not tier2:
            shell_class = SHELL_TIER1
        elif all(is_regulatory(req_id) for req_id in tier2):
            shell_class = SHELL_REGULATORY
        else:
            shell_class = SHELL_FUNCTIONAL
        volume_code = volumes[0] if len(volumes) == 1 else None
        if volume_code is None and shell_class != SHELL_PROCEDURAL:
            shell_class = SHELL_UNASSIGNED
        in_volume = [tier1_id for tier1_id in tier1_ids if tier1_volume.get(tier1_id) == volume_code]
        shells[test_id] = ShellPlacement(
            shell_id=test_id,
            title=test.title,
            requirement_ids=req_ids,
            tier1_ids=tier1_ids,
            volume_code=volume_code,
            tier1_id=in_volume[0] if volume_code and in_volume else None,
            shell_class=shell_class,
            evidence_ids=list(test.evidence_ids),
        )

    def home_key(shell_id: str) -> tuple[int, int, int, int]:
        shell = shells[shell_id]
        return (
            _HOME_RANK.get(shell.shell_class, len(_HOME_RANK)),
            volume_rank.get(shell.volume_code or "", len(volume_rank) + 1),
            tier1_rank.get(shell.tier1_id or "", len(tier1_rank)),
            shell_rank[shell_id],
        )

    bindings = document.evidence_bindings(hierarchy)

    # Home overrides.
    overrides: dict[str, tuple[str, Optional[str]]] = {}
    for spec in manifest.volumes:
        for evidence_id, shell_id in spec.homes.items():
            if evidence_id in overrides:
                errors.append(
                    f"evidence `{evidence_id}` has a home override in `{overrides[evidence_id][0]}` and `{spec.code}`"
                )
                continue
            overrides[evidence_id] = (spec.code, shell_id)
            binding = bindings.get(evidence_id)
            if binding is None:
                errors.append(f"volume `{spec.code}` overrides the home of `{evidence_id}`, which no shell binds")
            elif shell_id is not None:
                if shell_id not in shells:
                    errors.append(
                        f"volume `{spec.code}` homes `{evidence_id}` under `{shell_id}`, which is not a shell"
                    )
                elif shell_id not in binding.shell_ids:
                    errors.append(
                        f"volume `{spec.code}` homes `{evidence_id}` under `{shell_id}`, which does not bind it"
                    )
                elif shells[shell_id].volume_code != spec.code:
                    errors.append(
                        f"volume `{spec.code}` homes `{evidence_id}` under `{shell_id}`, "
                        f"which is in `{shells[shell_id].volume_code}`"
                    )
            elif not any(shells[candidate].volume_code == spec.code for candidate in binding.shell_ids):
                errors.append(f"volume `{spec.code}` homes `{evidence_id}`, but no shell in that volume binds it")

    # Evidence homes.
    evidence: dict[str, EvidenceHome] = {}
    for evidence_id, binding in bindings.items():
        candidates = [shell_id for shell_id in binding.shell_ids if shells[shell_id].volume_code]
        bound_volumes = sorted(
            _unique([shells[shell_id].volume_code or "" for shell_id in candidates]),
            key=lambda code: volume_rank.get(code, 0),
        )
        reason = ""
        home_shell: Optional[str] = None
        if evidence_id in overrides:
            override_volume, override_shell = overrides[evidence_id]
            in_volume = [shell_id for shell_id in candidates if shells[shell_id].volume_code == override_volume]
            if override_shell is not None and override_shell in in_volume:
                home_shell = override_shell
            elif override_shell is None and in_volume:
                home_shell = min(in_volume, key=home_key)
            reason = "override"
        elif candidates:
            home_shell = min(candidates, key=home_key)
            reason = shells[home_shell].shell_class
        elif not any(shells[shell_id].tier1_ids for shell_id in binding.shell_ids):
            # Shells that trace to a Tier 1 but have no volume are already reported (an
            # unassigned Tier 1 or a shell spanning volumes); only procedural or
            # requirement-less bindings are a problem of their own.
            errors.append(
                f"evidence `{evidence_id}` is bound only by shells outside every volume "
                f"({', '.join(binding.shell_ids)}); give it a home override or move the binding"
            )
        if home_shell is None:
            continue
        evidence[evidence_id] = EvidenceHome(
            evidence_id=evidence_id,
            volume_code=shells[home_shell].volume_code or "",
            shell_id=home_shell,
            reason=reason,
            bound_volumes=bound_volumes,
            binding=binding,
        )

    for shell in shells.values():
        for evidence_id in shell.evidence_ids:
            home = evidence.get(evidence_id)
            if home is None:
                continue
            if home.shell_id == shell.shell_id:
                shell.home_evidence_ids.append(evidence_id)
            else:
                shell.pointer_evidence_ids.append(evidence_id)

    if errors:
        raise VolumePartitionError(errors)
    return VolumePlan(
        manifest=manifest,
        hierarchy=hierarchy,
        tier1_volume=tier1_volume,
        requirement_volume=requirement_volume,
        shells=shells,
        evidence=evidence,
        procedural_requirement_ids=procedural,
        tier1_titles={tier1_id: requirements[tier1_id].title for tier1_id in corpus_tier1},
    )


def check_volumes(directory: str, manifest_path: str, *, version: str = "") -> tuple[dict, int]:
    """Parse the whole corpus, plan the volumes and return ``(report, exit_code)``.

    Anything the parser prints goes to stderr, so the caller can print the report alone on
    stdout.
    """
    try:
        manifest = load_manifest(manifest_path)
        with contextlib.redirect_stdout(sys.stderr):
            document = process_directory(directory, version=version)
            plan = plan_volumes(document, manifest)
    except (ManifestError, VolumePartitionError) as exc:
        return {"ok": False, "manifest": manifest_path, "errors": exc.errors}, 1
    except (OSError, ValueError) as exc:
        return {"ok": False, "manifest": manifest_path, "errors": [str(exc)]}, 1
    return plan.to_dict(), 0


def format_report(report: dict) -> str:
    return json.dumps(report, indent=2, ensure_ascii=False) + "\n"
