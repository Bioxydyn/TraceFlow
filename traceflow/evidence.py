"""Typed evidence records: one collected test result (an ``autotest`` ID).

A record is read from ``<test-results>/<id>/result.json`` (evidence record contract,
section 2) when the collector wrote one, and is otherwise parsed from ``result.md``, mapping
its bullets and sections onto the same fields. Figures carry the SHA-256 and size of the
image file on disk; a ``result.json`` hash that does not match the file fails the load.

The PDF generator renders from these fields. ``load_autotest_result`` also returns the
legacy ``result.md`` AST parts that today's inline renderer uses.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import struct
from dataclasses import dataclass, field
from typing import Optional

from traceflow.parser import (
    AutotestResult,
    extract_autotest_result,
    get_heading_text,
    is_ast_element_heading,
    parse_markdown,
)

RESULT_JSON = "result.json"
RESULT_MD = "result.md"
SUPPORTED_SCHEMAS = (1,)

_FILENAME_ALT = re.compile(r"[A-Za-z0-9._-]+\.(png|jpg|jpeg|gif|svg|pdf)", re.IGNORECASE)
_SHA256 = re.compile(r"[0-9a-f]{64}")
_REQUIREMENT_TOKEN = re.compile(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+")
_ASSERTION_IDS = re.compile(r"^\(([^)]*)\)\s*(.*)$", re.DOTALL)
# pytest prints "----- Captured Log -----" / "----- Captured stdout call -----" banners.
_CAPTURE_BANNER = re.compile(r"^-{3,} Captured [A-Za-z ]+ -{3,}$")
_UNAVAILABLE = {"", "<unavailable>", "unavailable", "n/a", "none", "-"}


class EvidenceError(ValueError):
    """An evidence record is missing, malformed or does not match its files."""


@dataclass
class EvidenceFigure:
    """One image saved for the record. ``path`` is relative to the record's folder."""

    path: str
    caption: str = ""
    sha256: str = ""
    size_bytes: Optional[int] = None
    width: Optional[int] = None
    height: Optional[int] = None
    requirement_ids: list[str] = field(default_factory=list)
    checked_at: Optional[str] = None

    @property
    def short_sha256(self) -> str:
        """First 12 hex characters of the SHA-256, as printed under each figure."""
        return self.sha256[:12]


@dataclass
class EvidenceAssertion:
    caption: str
    requirement_ids: list[str] = field(default_factory=list)
    # Up to 15 ``[check, expected, actual]`` rows, recorded after the assertion passed.
    rows: list[list[str]] = field(default_factory=list)
    checked_at: Optional[str] = None
    # Index into ``EvidenceRecord.figures``, or ``None``.
    figure: Optional[int] = None


@dataclass
class EvidenceOutput:
    lines: list[str] = field(default_factory=list)
    # ``None`` when the source does not say (``result.md``).
    total_lines: Optional[int] = None
    truncated: Optional[bool] = None

    @property
    def is_empty(self) -> bool:
        return not any(line.strip() for line in self.lines)


@dataclass
class EvidenceJob:
    name: str
    conclusion: str


@dataclass
class EvidenceRecord:
    """Contract section 2 fields for one evidence ID."""

    evidence_id: str
    # ``"result.json"`` or ``"result.md"``: the file the fields came from.
    origin: str
    result_dir: str
    schema: Optional[int] = None
    kind: str = ""
    status: str = ""
    workflow_path: str = ""
    workflow_name: str = ""
    run_id: Optional[int] = None
    run_url: str = ""
    head_sha: str = ""
    source: str = ""
    duration_s: Optional[float] = None
    description: str = ""
    description_source: str = ""
    assertions: list[EvidenceAssertion] = field(default_factory=list)
    figures: list[EvidenceFigure] = field(default_factory=list)
    output: EvidenceOutput = field(default_factory=EvidenceOutput)
    jobs: list[EvidenceJob] = field(default_factory=list)

    @property
    def workflow(self) -> str:
        return self.workflow_path

    @property
    def passed(self) -> bool:
        return self.status.strip().lower() in {"passed", "pass", "success", "succeeded"}

    @property
    def status_label(self) -> str:
        """``passed`` -> ``Passed``, ``in_progress`` -> ``In Progress``."""
        cleaned = re.sub(r"[_-]+", " ", self.status.strip())
        return " ".join(part.capitalize() for part in cleaned.split())

    @property
    def duration_text(self) -> str:
        if self.duration_s is None:
            return ""
        return f"{self.duration_s:.3f} s"

    @property
    def assertion_captions(self) -> list[str]:
        return [assertion.caption for assertion in self.assertions]

    @property
    def requirement_ids(self) -> list[str]:
        """Requirement IDs named by the assertions, in order, once each."""
        found: list[str] = []
        for assertion in self.assertions:
            for req_id in assertion.requirement_ids:
                if req_id not in found:
                    found.append(req_id)
        return found

    @property
    def missing_fields(self) -> list[str]:
        """Fields the evidence gate warns about (``WARNING: autotest ... missing required fields``)."""
        missing: list[str] = []
        if not self.description.strip():
            missing.append("description")
        if not self.assertions:
            missing.append("assertions")
        return missing


# ------------------------------------------------------------------ file facts


def _image_dimensions(data: bytes) -> tuple[Optional[int], Optional[int]]:
    """Width and height from a PNG, GIF or JPEG header; ``(None, None)`` otherwise."""
    if data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24 and data[12:16] == b"IHDR":
        width, height = struct.unpack(">II", data[16:24])
        return width, height
    if data[:6] in (b"GIF87a", b"GIF89a") and len(data) >= 10:
        width, height = struct.unpack("<HH", data[6:10])
        return width, height
    if data.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 9 < len(data):
            if data[offset] != 0xFF:
                offset += 1
                continue
            marker = data[offset + 1]
            if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                offset += 2
                continue
            (length,) = struct.unpack(">H", data[offset + 2:offset + 4])
            if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                height, width = struct.unpack(">HH", data[offset + 5:offset + 9])
                return width, height
            offset += 2 + length
    return None, None


def _resolve_inside(result_dir: str, relative_path: str) -> str:
    base = os.path.realpath(result_dir)
    resolved = os.path.realpath(os.path.join(base, relative_path))
    if resolved != base and not resolved.startswith(base + os.sep):
        raise EvidenceError(f"Figure path `{relative_path}` points outside its record folder {result_dir}")
    return resolved


def _fill_file_facts(figure: EvidenceFigure, result_dir: str, *, required: bool, evidence_id: str) -> None:
    """Set ``sha256``, ``size_bytes`` and (if unset) ``width``/``height`` from the file.

    A declared SHA-256 must match the file. With ``required`` a missing file fails.
    """
    if figure.path.lower().startswith(("http://", "https://")):
        return
    absolute = _resolve_inside(result_dir, figure.path)
    if not os.path.isfile(absolute):
        if required:
            raise EvidenceError(f"Evidence `{evidence_id}` lists figure `{figure.path}`, which is not in {result_dir}")
        return
    with open(absolute, "rb") as handle:
        data = handle.read()
    actual = hashlib.sha256(data).hexdigest()
    if figure.sha256 and figure.sha256 != actual:
        raise EvidenceError(
            f"Evidence `{evidence_id}` figure `{figure.path}`: result.json gives SHA-256 {figure.sha256}, "
            f"but the file hashes to {actual}"
        )
    figure.sha256 = actual
    figure.size_bytes = len(data)
    if figure.width is None or figure.height is None:
        width, height = _image_dimensions(data)
        figure.width = figure.width if figure.width is not None else width
        figure.height = figure.height if figure.height is not None else height


def _read_sidecar(result_dir: str, relative_path: str) -> dict:
    sidecar = os.path.join(result_dir, os.path.splitext(relative_path)[0] + ".json")
    if not os.path.isfile(sidecar):
        return {}
    try:
        with open(sidecar, "r", encoding="utf-8") as handle:
            loaded = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _sidecar_requirement_ids(sidecar: dict) -> list[str]:
    ids = sidecar.get("requirementIds")
    if isinstance(ids, list):
        return [str(item).strip() for item in ids if str(item).strip()]
    single = sidecar.get("requirementId")
    if isinstance(single, str) and single.strip():
        return [single.strip()]
    return []


def _link_assertions_to_figures(assertions: list[EvidenceAssertion], figures: list[EvidenceFigure]) -> None:
    """``result.md`` has no explicit link: an assertion owns the first unclaimed figure whose
    caption matches it, and inherits that figure's ``checkedAt`` and requirement IDs."""
    claimed: set[int] = set()
    for assertion in assertions:
        for index, figure in enumerate(figures):
            if index in claimed or figure.caption.strip() != assertion.caption.strip():
                continue
            claimed.add(index)
            assertion.figure = index
            if assertion.checked_at is None:
                assertion.checked_at = figure.checked_at
            if not assertion.requirement_ids:
                assertion.requirement_ids = list(figure.requirement_ids)
            break


# ------------------------------------------------------------------ result.md


def _inline_text(node: dict) -> str:
    """Flatten inline Markdown back to its text, keeping inline HTML (``<docId>``), code spans
    and emphasis markers instead of dropping them."""
    node_type = node.get("type")
    if node_type == "text":
        return str(node.get("text", ""))
    if node_type == "codespan":
        return f"`{node.get('text', '')}`"
    if node_type == "inline_html":
        return str(node.get("text", ""))
    if node_type in {"softbreak", "linebreak"}:
        return " "
    children = "".join(_inline_text(child) for child in node.get("children", []) or [])
    if node_type == "emphasis":
        return f"*{children}*"
    if node_type == "strong":
        return f"**{children}**"
    if node_type == "link" and not children:
        return str(node.get("link", ""))
    if node_type == "image":
        return ""
    return children


def _bullet_text(list_item: dict) -> str:
    parts: list[str] = []
    for child in list_item.get("children", []):
        if child.get("type") in {"block_text", "paragraph"}:
            parts.append("".join(_inline_text(grandchild) for grandchild in child.get("children", [])))
    return " ".join(part.strip() for part in parts if part.strip())


def _strip_codespan(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value.startswith("`") and value.endswith("`"):
        return value[1:-1].strip()
    return value


def _parse_duration(value: str) -> Optional[float]:
    match = re.fullmatch(r"([0-9]+(?:\.[0-9]+)?)\s*(ms|s)?", _strip_codespan(value))
    if not match:
        return None
    number = float(match.group(1))
    return number / 1000.0 if match.group(2) == "ms" else number


def _parse_run_id(value: str) -> Optional[int]:
    cleaned = _strip_codespan(value)
    return int(cleaned) if cleaned.isdigit() else None


def _available(value: str) -> str:
    cleaned = _strip_codespan(value)
    return "" if cleaned.strip().lower() in _UNAVAILABLE else cleaned


_MD_LABELS = {
    "kind": "kind",
    "status": "status",
    "workflow": "workflow_path",
    "workflow path": "workflow_path",
    "workflow name": "workflow_name",
    "source": "source",
    "description": "description",
    "description source": "description_source",
    "run id": "run_id",
    "run url": "run_url",
    "duration": "duration",
    "head sha": "head_sha",
    "commit": "head_sha",
}


def _section_name(heading: str) -> str:
    lowered = heading.strip().lower().rstrip(":")
    if lowered == "assertions":
        return "assertions"
    if lowered.startswith("output"):
        return "output"
    if lowered in {"screenshots", "figures"}:
        return "figures"
    return "other"


def _md_assertions(items: list[dict]) -> list[EvidenceAssertion]:
    texts: list[str] = []

    def walk_list(list_node: dict) -> None:
        for list_item in list_node.get("children", []):
            text = _bullet_text(list_item)
            if text:
                texts.append(text)
            for child in list_item.get("children", []):
                if child.get("type") == "list":
                    walk_list(child)

    for elem in items:
        if elem.get("type") == "list":
            walk_list(elem)
        elif elem.get("type") == "paragraph":
            text = "".join(_inline_text(child) for child in elem.get("children", [])).strip()
            if text:
                texts.append(text)

    assertions: list[EvidenceAssertion] = []
    for text in texts:
        requirement_ids: list[str] = []
        caption = text
        match = _ASSERTION_IDS.match(text)
        if match:
            tokens = [token for token in re.split(r"[,\s]+", match.group(1)) if token]
            if tokens and all(_REQUIREMENT_TOKEN.fullmatch(token) for token in tokens):
                requirement_ids = tokens
                caption = match.group(2).strip()
        assertions.append(EvidenceAssertion(caption=caption, requirement_ids=requirement_ids))
    return assertions


def _md_output(items: list[dict]) -> EvidenceOutput:
    for elem in items:
        if elem.get("type") == "block_code":
            lines = str(elem.get("text", "")).rstrip("\n").split("\n")
            kept = [line for line in lines if not _CAPTURE_BANNER.match(line.strip())]
            return EvidenceOutput(lines=kept if any(line.strip() for line in kept) else [])
    return EvidenceOutput()


def _md_figures(parsed: list[dict], result_dir: str, evidence_id: str) -> list[EvidenceFigure]:
    figures: list[EvidenceFigure] = []

    def visit(nodes: list[dict]) -> None:
        pending_caption = ""
        for index, node in enumerate(nodes):
            node_type = node.get("type")
            if node_type == "strong" and _inline_text(node).strip("* ").lower() == "caption:":
                following = nodes[index + 1] if index + 1 < len(nodes) else {}
                pending_caption = str(following.get("text", "")).strip() if following.get("type") == "text" else ""
                continue
            if node_type == "image":
                src = str(node.get("src", "")).replace("\\", "/")
                if src.startswith("./"):
                    src = src[2:]
                sidecar = _read_sidecar(result_dir, src) if not src.startswith(("http://", "https://")) else {}
                alt = str(node.get("alt", "") or "").strip()
                caption = str(sidecar.get("caption") or "").strip()
                if not caption:
                    caption = pending_caption or ("" if _FILENAME_ALT.fullmatch(alt) else alt)
                checked_at = sidecar.get("checkedAt")
                figure = EvidenceFigure(
                    path=src,
                    caption=caption,
                    requirement_ids=_sidecar_requirement_ids(sidecar),
                    checked_at=str(checked_at) if checked_at else None,
                )
                _fill_file_facts(figure, result_dir, required=False, evidence_id=evidence_id)
                figures.append(figure)
                pending_caption = ""
                continue
            if node.get("children"):
                visit(node["children"])

    visit(parsed)
    return figures


def _md_metadata_bullets(markdown_text: str) -> list[str]:
    """The headline ``- Label: value`` bullets, from the raw text before the first H2.

    Read from the text rather than the AST because mistune 2 ends a list item at a lazy
    continuation line, which cut multi-line descriptions short and split the bullets.
    """
    bullets: list[list[str]] = []
    in_bullet = False
    for line in markdown_text.splitlines():
        if line.startswith("## "):
            break
        if line.startswith("# "):
            in_bullet = False
            continue
        match = re.match(r"^\s{0,3}[-*+]\s+(.*)$", line)
        if match:
            bullets.append([match.group(1).strip()])
            in_bullet = True
        elif in_bullet and line.strip():
            bullets[-1].append(line.strip())
        else:
            in_bullet = False
    return [" ".join(parts) for parts in bullets]


def _markdown_inline_text(text: str) -> str:
    """Render one line of inline Markdown to plain text (escapes undone, HTML kept)."""
    blocks = parse_markdown(text)
    return " ".join(
        "".join(_inline_text(child) for child in block.get("children", [])).strip()
        for block in blocks
        if block.get("type") in {"paragraph", "block_text"}
    ).strip()


def parse_result_md(markdown_text: str, *, result_dir: str, evidence_id: str) -> EvidenceRecord:
    """Map a collector ``result.md`` onto the typed fields."""
    parsed = parse_markdown(markdown_text)
    record = EvidenceRecord(evidence_id=evidence_id, origin=RESULT_MD, result_dir=result_dir)
    sections: dict[str, list[dict]] = {"assertions": [], "output": [], "figures": [], "other": []}
    section: Optional[str] = None
    for elem in parsed:
        level = is_ast_element_heading(elem)
        if level == 1:
            heading = get_heading_text(elem)
            if heading and heading != evidence_id:
                raise EvidenceError(f"{result_dir}/{RESULT_MD} is headed `{heading}`, expected `{evidence_id}`")
            continue
        if level == 2:
            section = _section_name(get_heading_text(elem))
            continue
        if section is not None:
            sections[section].append(elem)

    for bullet in _md_metadata_bullets(markdown_text):
        _apply_md_metadata(record, _markdown_inline_text(bullet))
    record.assertions = _md_assertions(sections["assertions"])
    record.output = _md_output(sections["output"])
    record.figures = _md_figures(parsed, result_dir, evidence_id)
    _link_assertions_to_figures(record.assertions, record.figures)
    return record


def _apply_md_metadata(record: EvidenceRecord, text: str) -> None:
    label, separator, value = text.partition(":")
    if not separator:
        return
    key = _MD_LABELS.get(label.strip().lower())
    if key is None:
        return
    value = value.strip()
    if key == "run_id":
        record.run_id = _parse_run_id(value)
    elif key == "duration":
        record.duration_s = _parse_duration(value)
    elif key in {"run_url", "head_sha"}:
        setattr(record, key, _available(value))
    elif key == "description":
        record.description = value
    else:
        setattr(record, key, _strip_codespan(value))


# ------------------------------------------------------------------ result.json


def _json_str(data: dict, key: str, evidence_id: str, *, required: bool = False) -> str:
    value = data.get(key)
    if value is None:
        if required:
            raise EvidenceError(f"Evidence `{evidence_id}` result.json has no `{key}`")
        return ""
    if not isinstance(value, str):
        raise EvidenceError(f"Evidence `{evidence_id}` result.json `{key}` must be a string")
    return value


def _json_opt_int(value: object, what: str, evidence_id: str) -> Optional[int]:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        raise EvidenceError(f"Evidence `{evidence_id}` result.json `{what}` must be an integer")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    raise EvidenceError(f"Evidence `{evidence_id}` result.json `{what}` must be an integer")


def _json_opt_float(value: object, what: str, evidence_id: str) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    raise EvidenceError(f"Evidence `{evidence_id}` result.json `{what}` must be a number")


def _json_list(data: dict, key: str, evidence_id: str) -> list:
    value = data.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise EvidenceError(f"Evidence `{evidence_id}` result.json `{key}` must be a list")
    return value


def _json_dict(value: object, what: str, evidence_id: str) -> dict:
    if not isinstance(value, dict):
        raise EvidenceError(f"Evidence `{evidence_id}` result.json `{what}` must be an object")
    return value


def _json_cell(value: object, evidence_id: str) -> str:
    if isinstance(value, (dict, list)):
        raise EvidenceError(f"Evidence `{evidence_id}` result.json assertion rows must hold plain values")
    return "" if value is None else str(value)


def parse_result_json(data: object, *, result_dir: str, evidence_id: str) -> EvidenceRecord:
    """Read a contract section 2 ``result.json`` (schema 1), checking its figures on disk."""
    data = _json_dict(data, "document", evidence_id)
    schema = data.get("schema")
    if schema not in SUPPORTED_SCHEMAS:
        raise EvidenceError(f"Evidence `{evidence_id}` result.json has unsupported schema {schema!r}")
    declared_id = _json_str(data, "id", evidence_id, required=True)
    if declared_id != evidence_id:
        raise EvidenceError(f"{result_dir}/{RESULT_JSON} is for `{declared_id}`, expected `{evidence_id}`")

    figures: list[EvidenceFigure] = []
    for position, raw in enumerate(_json_list(data, "figures", evidence_id)):
        entry = _json_dict(raw, f"figures[{position}]", evidence_id)
        path = _json_str(entry, "path", evidence_id, required=True).replace("\\", "/")
        sha256 = _json_str(entry, "sha256", evidence_id).lower()
        if sha256 and not _SHA256.fullmatch(sha256):
            raise EvidenceError(f"Evidence `{evidence_id}` figure `{path}` has a malformed sha256")
        figure = EvidenceFigure(
            path=path,
            caption=_json_str(entry, "caption", evidence_id),
            sha256=sha256,
            width=_json_opt_int(entry.get("width"), f"figures[{position}].width", evidence_id),
            height=_json_opt_int(entry.get("height"), f"figures[{position}].height", evidence_id),
        )
        _fill_file_facts(figure, result_dir, required=True, evidence_id=evidence_id)
        figures.append(figure)

    assertions: list[EvidenceAssertion] = []
    for position, raw in enumerate(_json_list(data, "assertions", evidence_id)):
        entry = _json_dict(raw, f"assertions[{position}]", evidence_id)
        requirement_ids = _json_list(entry, "requirement_ids", evidence_id)
        if not all(isinstance(item, str) for item in requirement_ids):
            raise EvidenceError(f"Evidence `{evidence_id}` assertions[{position}].requirement_ids must be strings")
        rows: list[list[str]] = []
        for row in _json_list(entry, "rows", evidence_id):
            if not isinstance(row, list):
                raise EvidenceError(f"Evidence `{evidence_id}` assertions[{position}].rows must be lists")
            rows.append([_json_cell(cell, evidence_id) for cell in row])
        figure_index = _json_opt_int(entry.get("figure"), f"assertions[{position}].figure", evidence_id)
        if figure_index is not None and not 0 <= figure_index < len(figures):
            raise EvidenceError(
                f"Evidence `{evidence_id}` assertions[{position}].figure {figure_index} is not a figure index"
            )
        checked_at = entry.get("checked_at")
        assertion = EvidenceAssertion(
            caption=_json_str(entry, "caption", evidence_id),
            requirement_ids=list(requirement_ids),
            rows=rows,
            checked_at=str(checked_at) if checked_at else None,
            figure=figure_index,
        )
        if figure_index is not None:
            figure = figures[figure_index]
            figure.requirement_ids = figure.requirement_ids or list(assertion.requirement_ids)
            figure.checked_at = figure.checked_at or assertion.checked_at
        assertions.append(assertion)

    output_raw = data.get("output")
    output = EvidenceOutput()
    if output_raw is not None:
        output_data = _json_dict(output_raw, "output", evidence_id)
        lines = _json_list(output_data, "lines", evidence_id)
        if not all(isinstance(line, str) for line in lines):
            raise EvidenceError(f"Evidence `{evidence_id}` output.lines must be strings")
        truncated = output_data.get("truncated")
        if truncated is not None and not isinstance(truncated, bool):
            raise EvidenceError(f"Evidence `{evidence_id}` output.truncated must be true or false")
        output = EvidenceOutput(
            lines=list(lines),
            total_lines=_json_opt_int(output_data.get("total_lines"), "output.total_lines", evidence_id),
            truncated=truncated,
        )

    jobs: list[EvidenceJob] = []
    for position, raw in enumerate(_json_list(data, "jobs", evidence_id)):
        entry = _json_dict(raw, f"jobs[{position}]", evidence_id)
        jobs.append(
            EvidenceJob(
                name=_json_str(entry, "name", evidence_id),
                conclusion=_json_str(entry, "conclusion", evidence_id),
            )
        )

    return EvidenceRecord(
        evidence_id=evidence_id,
        origin=RESULT_JSON,
        result_dir=result_dir,
        schema=int(schema) if isinstance(schema, int) else None,
        kind=_json_str(data, "kind", evidence_id),
        status=_json_str(data, "status", evidence_id),
        workflow_path=_json_str(data, "workflow_path", evidence_id),
        workflow_name=_json_str(data, "workflow_name", evidence_id),
        run_id=_json_opt_int(data.get("run_id"), "run_id", evidence_id),
        run_url=_json_str(data, "run_url", evidence_id),
        head_sha=_json_str(data, "head_sha", evidence_id),
        source=_json_str(data, "source", evidence_id),
        duration_s=_json_opt_float(data.get("duration_s"), "duration_s", evidence_id),
        description=_json_str(data, "description", evidence_id),
        description_source=_json_str(data, "description_source", evidence_id),
        assertions=assertions,
        figures=figures,
        output=output,
        jobs=jobs,
    )


# ------------------------------------------------------------------ loaders


def _check_md_agrees(json_record: EvidenceRecord, md_record: EvidenceRecord) -> None:
    """``result.md`` feeds the evidence gate; it must not disagree with ``result.json``."""
    for name in ("status", "kind"):
        json_value = getattr(json_record, name).strip().lower()
        md_value = getattr(md_record, name).strip().lower()
        if json_value and md_value and json_value != md_value:
            raise EvidenceError(
                f"Evidence `{json_record.evidence_id}`: result.json {name} `{json_value}` disagrees with "
                f"result.md `{md_value}`"
            )


def load_evidence_record(result_dir: str, evidence_id: str) -> EvidenceRecord:
    """Load the typed record from ``result.json`` if present, else from ``result.md``."""
    json_path = os.path.join(result_dir, RESULT_JSON)
    md_path = os.path.join(result_dir, RESULT_MD)
    md_record: Optional[EvidenceRecord] = None
    if os.path.isfile(md_path):
        with open(md_path, "r", encoding="utf-8") as handle:
            md_record = parse_result_md(handle.read(), result_dir=result_dir, evidence_id=evidence_id)
    if os.path.isfile(json_path):
        try:
            with open(json_path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except json.JSONDecodeError as exc:
            raise EvidenceError(f"{json_path} is not valid JSON: {exc}") from exc
        record = parse_result_json(data, result_dir=result_dir, evidence_id=evidence_id)
        if md_record is not None:
            _check_md_agrees(record, md_record)
        return record
    if md_record is not None:
        return md_record
    raise FileNotFoundError(
        f"Autotest result missing for `{evidence_id}`: no {RESULT_JSON} or {RESULT_MD} in {result_dir}"
    )


def load_autotest_result(result_dir: str, evidence_id: str) -> AutotestResult:
    """The legacy ``result.md`` parts plus the typed ``record``.

    ``missing_fields`` follows the typed record when it came from ``result.json``.
    """
    record = load_evidence_record(result_dir, evidence_id)
    md_path = os.path.join(result_dir, RESULT_MD)
    if os.path.isfile(md_path):
        with open(md_path, "r", encoding="utf-8") as handle:
            autotest = extract_autotest_result(parse_markdown(handle.read()), test_id=evidence_id)
    else:
        autotest = AutotestResult(test_id=evidence_id, description=record.description)
    if record.origin == RESULT_JSON:
        autotest.missing_fields = record.missing_fields
    autotest.record = record
    return autotest
