"""LaTeX building blocks for evidence records, and the evidence index.

An evidence record (contract section 3) is printed with an ``Evidence <id>`` header that
carries a label and a named destination, ``ev:<id>``. After the last pdflatex pass the
page of every such label is read back from the ``.aux`` file and written to an evidence
index JSON (``{id: {volume, page, status, ...}}``), so another document can cite the
record as ``<volume code> p.<page>``.

The helpers here are plain functions; ``traceflow.pdf_generator.PdfReport`` assembles them.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from typing import Iterable, Optional, cast

LABEL_PREFIX = "ev:"
FIGURE_LABEL_PREFIX = "evfig:"
INDEX_SUFFIX = ".evidence-index.json"

LATEX_TEXT_ESCAPES = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

# Characters after which a long path, ID or code span may break across lines.
_BREAK_AFTER = frozenset("/._:-=,;&?|@")
_SAFE_ANCHOR = re.compile(r"[A-Za-z0-9._-]+")
_LONG_RUN = 16


def latex_escape(text: str) -> str:
    """Escape every LaTeX special character, for text or ``\\texttt``."""
    return "".join(LATEX_TEXT_ESCAPES.get(ch, ch) for ch in text)


def breakable(text: str, *, run: int = _LONG_RUN, replacements: Optional[dict[str, str]] = None) -> str:
    """``text`` escaped, with a line-break point after ``/ . _ : -`` and the like.

    A run of ``run`` characters with no such point (a hash, a long word) also gets one,
    so no path, ID or value can push past the margin. No hyphen is added at a break.
    ``replacements`` maps characters to ready-made LaTeX (``\u2264`` -> ``\\(\\leq\\)``).
    """
    out: list[str] = []
    since_break = 0
    for ch in text:
        if replacements and ch in replacements:
            out.append(replacements[ch])
            since_break += 1
            continue
        out.append(LATEX_TEXT_ESCAPES.get(ch, ch))
        if ch in _BREAK_AFTER or ch.isspace():
            out.append(r"\allowbreak{}" if not ch.isspace() else "")
            since_break = 0
            continue
        since_break += 1
        if since_break >= run:
            out.append(r"\allowbreak{}")
            since_break = 0
    return "".join(out)


def breakable_code(text: str, replacements: Optional[dict[str, str]] = None) -> str:
    r"""``\texttt`` that escapes its text and may break after ``/ . _ : -``."""
    return r"\texttt{" + breakable(text, replacements=replacements) + "}"


def evidence_anchor(evidence_id: str) -> str:
    """The label and named destination of a record: ``ev:<id>``.

    An ID with characters outside ``A-Z a-z 0-9 . _ -`` gets a slug plus a short hash, so
    the name stays valid for LaTeX and unique.
    """
    if _SAFE_ANCHOR.fullmatch(evidence_id):
        return LABEL_PREFIX + evidence_id
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", evidence_id).strip("-") or "id"
    digest = hashlib.sha256(evidence_id.encode("utf-8")).hexdigest()[:8]
    return f"{LABEL_PREFIX}{slug}-{digest}"


def status_colour(value: str) -> str:
    status = value.strip().lower()
    if status in {"passed", "pass", "success", "succeeded"}:
        return "green!50!black"
    if status in {"failed", "fail", "error", "failure"}:
        return "red!70!black"
    if status in {"skipped", "skip"}:
        return "gray!70!black"
    if status in {"running", "in progress", "in_progress"}:
        return "blue!65!black"
    if status in {"cancelled", "canceled", "warning"}:
        return "orange!80!black"
    return "black"


def status_label(value: str) -> str:
    """``passed`` -> ``Passed``, ``in_progress`` -> ``In Progress``; empty -> ``Unknown``."""
    cleaned = re.sub(r"[_-]+", " ", value.strip())
    return " ".join(part.capitalize() for part in cleaned.split()) or "Unknown"


def coloured_status(value: str) -> str:
    return r"\textbf{\textcolor{" + status_colour(value) + "}{" + latex_escape(status_label(value)) + "}}"


# Console output is printed in an lstlisting, which cannot take UTF-8 multi-byte
# characters under pdflatex. Known symbols map to ASCII; anything else becomes ``?``.
_LISTING_REPLACEMENTS = {
    "✓": "PASS", "✔": "PASS", "✗": "FAIL", "✘": "FAIL",
    "→": "->", "←": "<-", "⇒": "=>", "•": "-", "·": "-", "…": "...",
    "“": '"', "”": '"', "‘": "'", "’": "'",
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
    "×": "x", "≤": "<=", "≥": ">=", "≠": "!=", "µ": "u", "μ": "u",
    " ": " ", "❯": ">", "⚠": "!",
}


def listing_ascii(line: str) -> str:
    """One console line made safe for an lstlisting: ASCII only, no control characters."""
    out: list[str] = []
    for ch in line:
        code = ord(ch)
        if code < 32 or code == 127:
            out.append("    " if ch == "\t" else "")
        elif 32 <= code < 127:
            out.append(ch)
        elif ch in _LISTING_REPLACEMENTS:
            out.append(_LISTING_REPLACEMENTS[ch])
        elif 0x2580 <= code <= 0x259F:
            out.append("#")
        elif 0x2500 <= code <= 0x257F:
            out.append("-" if "HORIZONTAL" in unicodedata.name(ch, "") else "|")
        else:
            plain = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
            out.append(plain if plain and all(32 <= ord(c) < 127 for c in plain) else "?")
    text = "".join(out)
    # A literal end of the environment would close the listing early.
    return text.replace(r"\end{lstlisting}", r"\end {lstlisting}")


# ------------------------------------------------------------------ the .aux file


def _braced_groups(text: str, start: int, count: int) -> Optional[list[str]]:
    """``count`` consecutive ``{...}`` groups from ``text[start:]`` (nested braces kept)."""
    groups: list[str] = []
    position = start
    while len(groups) < count:
        while position < len(text) and text[position] in " \t":
            position += 1
        if position >= len(text) or text[position] != "{":
            return None
        depth = 0
        begin = position
        while position < len(text):
            char = text[position]
            if char == "\\":
                position += 2
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    break
            position += 1
        if depth != 0:
            return None
        groups.append(text[begin + 1:position])
        position += 1
    return groups


def parse_aux_pages(aux_text: str, prefix: str = LABEL_PREFIX) -> dict[str, str]:
    r"""Label name -> page, for every ``\newlabel{<prefix>...}`` in a LaTeX ``.aux`` file.

    With hyperref a label reads ``\newlabel{name}{{number}{page}{title}{anchor}{}}``.
    """
    pages: dict[str, str] = {}
    marker = r"\newlabel{"
    position = aux_text.find(marker)
    while position != -1:
        groups = _braced_groups(aux_text, position + len(marker) - 1, 2)
        if groups is not None:
            name, body = groups
            if name.startswith(prefix):
                fields = _braced_groups(body, 0, 2)
                if fields is not None:
                    pages[name] = fields[1]
        position = aux_text.find(marker, position + len(marker))
    return pages


def undefined_references(log_text: str, prefix: str = "") -> list[str]:
    """Labels a LaTeX ``.log`` reports as undefined (optionally only those with ``prefix``)."""
    found: list[str] = []
    for match in re.finditer(r"Reference `([^']+)' on page \S+ undefined", log_text):
        name = match.group(1)
        if name.startswith(prefix) and name not in found:
            found.append(name)
    return found


# ------------------------------------------------------------------ evidence index


class EvidenceIndexError(ValueError):
    """An evidence index file is malformed, or two files claim the same record."""


def write_evidence_index(path: str, entries: dict[str, dict]) -> None:
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(entries, handle, indent=2, ensure_ascii=False)
        handle.write("\n")


def _check_index_entry(path: str, evidence_id: object, entry: object) -> dict:
    if not isinstance(evidence_id, str) or not evidence_id:
        raise EvidenceIndexError(f"{path}: every key must be an evidence ID")
    if not isinstance(entry, dict):
        raise EvidenceIndexError(f"{path}: `{evidence_id}` must map to an object")
    entry = cast("dict[str, object]", entry)
    volume = entry.get("volume")
    page = entry.get("page")
    status = entry.get("status")
    if not isinstance(volume, str) or not volume:
        raise EvidenceIndexError(f"{path}: `{evidence_id}` has no `volume`")
    if isinstance(page, bool) or not isinstance(page, (int, str)) or str(page).strip() == "":
        raise EvidenceIndexError(f"{path}: `{evidence_id}` has no `page`")
    if not isinstance(status, str):
        raise EvidenceIndexError(f"{path}: `{evidence_id}` has no `status`")
    return dict(entry)


def load_evidence_index(paths: Iterable[str]) -> dict[str, dict]:
    """Merge evidence index files. A record listed by two files is an error."""
    merged: dict[str, dict] = {}
    origin: dict[str, str] = {}
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except FileNotFoundError as exc:
            raise EvidenceIndexError(f"Evidence index {path} does not exist") from exc
        except json.JSONDecodeError as exc:
            raise EvidenceIndexError(f"Evidence index {path} is not valid JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise EvidenceIndexError(f"Evidence index {path} must map evidence IDs to entries")
        for evidence_id, entry in data.items():
            checked = _check_index_entry(path, evidence_id, entry)
            if evidence_id in merged:
                raise EvidenceIndexError(
                    f"Evidence `{evidence_id}` is listed by two evidence indexes: {origin[evidence_id]} "
                    f"({merged[evidence_id]['volume']}) and {path} ({checked['volume']})"
                )
            merged[evidence_id] = checked
            origin[evidence_id] = path
    return merged


def default_index_path(output_pdf: str) -> str:
    return os.path.splitext(output_pdf)[0] + INDEX_SUFFIX
