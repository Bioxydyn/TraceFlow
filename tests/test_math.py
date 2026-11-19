"""Maths: read only with config.yml ``math: true``, only in the documents, never in evidence.

Since 0.0.31 a ``$`` has printed as written (DICOM text, prices and shell variables are full of
them), so maths is opt-in: without ``math: true`` nothing changes.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_evidence_render import _FakeLatex
from traceflow.parser import parse_markdown, process_directory
from traceflow.pdf_generator import PdfReport


def _math(nodes: list[dict]) -> list[tuple[str, bool]]:
    found: list[tuple[str, bool]] = []
    for node in nodes:
        if node["type"] == "math":
            found.append((node["text"], node["display"]))
        found.extend(_math(node.get("children", [])))
    return found


def _text(nodes: list[dict]) -> str:
    return "".join(node.get("text", "") + _text(node.get("children", [])) for node in nodes if node["type"] != "math")


def test_math_is_off_by_default() -> None:
    markdown = "The entropy is $H = L \\times \\log_2(N)$.\n\n$$ x^2 $$\n"
    assert _math(parse_markdown(markdown)) == []
    assert "$H = L \\times \\log_2(N)$" in _text(parse_markdown(markdown))


def test_math_reads_inline_and_display_formulas_in_paragraphs_lists_and_tables() -> None:
    markdown = (
        "The entropy is $H = L \\times \\log_2(N)$ bits, with $x_{i}$ and $a*b*c$.\n\n"
        "$$\n\\begin{aligned} a &= b \\\\ c &= d \\end{aligned}\n$$\n\n"
        "- an item with $\\alpha$\n\n"
        "| Symbol | Meaning |\n|---|---|\n| $N$ | symbols |\n"
    )
    assert _math(parse_markdown(markdown, math=True)) == [
        ("H = L \\times \\log_2(N)", False),
        ("x_{i}", False),
        ("a*b*c", False),
        ("\n\\begin{aligned} a &= b \\\\ c &= d \\end{aligned}\n", True),
        ("\\alpha", False),
        ("N", False),
    ]


@pytest.mark.parametrize(
    ("markdown", "text"),
    [
        ("It costs $20 and $30.", "It costs $20 and $30."),  # a closing $ followed by a digit
        ("Set $HOME and $PATH.", "Set $HOME and $PATH."),  # a closing $ after a space
        ("A literal \\$5 and \\$x\\$.", "A literal $5 and $x$."),  # \$ is a dollar
        ("Run `echo $HOME$` here.", "Run echo $HOME$ here."),  # code spans are never maths
    ],
)
def test_math_leaves_prices_variables_escapes_and_code_as_text(markdown: str, text: str) -> None:
    parsed = parse_markdown(markdown, math=True)
    assert _math(parsed) == [] and _text(parsed) == text


def test_math_in_headings_and_emphasis_stays_as_written() -> None:
    parsed = parse_markdown("## REQ-001: Speed $v$ limit\n\n*about $x$*\n", math=True)
    assert _math(parsed) == []
    assert parsed[0]["children"] == [{"type": "text", "text": "REQ-001: Speed $v$ limit"}]
    assert parsed[1]["children"][0]["children"] == [{"type": "text", "text": "about $x$"}]


def test_math_with_unbalanced_braces_fails() -> None:
    with pytest.raises(ValueError, match=r"The maths `\\frac\{a\}\{b` has unbalanced braces"):
        parse_markdown("$\\frac{a}{b$", math=True)


def test_config_math_must_be_true_or_false(tmp_path: Path) -> None:
    (tmp_path / "config.yml").write_text("math: yes please\n")
    with pytest.raises(ValueError, match="`math` must be true or false"):
        process_directory(str(tmp_path), version="1")


def _corpus(root: Path, *, math: bool) -> tuple[Path, Path]:
    (root / "requirements").mkdir(parents=True)
    (root / "tests").mkdir()
    if math:
        (root / "config.yml").write_text("math: true\n")
    (root / "requirements" / "reqs.md").write_text(
        "# Requirements\n\n## REQ-001: Entropy\n\nThe entropy is $H = L \\times \\log_2(N)$ bits:\n\n"
        "$$ H = L \\times \\log_2(N) $$\n"
    )
    (root / "tests" / "tests.md").write_text(
        "# Tests\n\n## TEST-001: Entropy\n\n**Requirement ID:** REQ-001\n\n```autotest\nPY-ENTROPY\n```\n\n"
        "```autotest\nPY-ENTROPY-MD\n```\n"
    )
    results = root.parent / "results"
    (results / "PY-ENTROPY").mkdir(parents=True)
    record = {
        "schema": 1, "id": "PY-ENTROPY", "kind": "test", "status": "passed", "workflow_path": "ci.yml",
        "workflow_name": "CI", "run_id": 1, "run_url": None, "head_sha": None, "source": "test_entropy.py",
        "duration_s": 0.1, "description": "Checks $HOME/$USER and a $5 fee.",
        "assertions": [{"caption": "Prints $x$ as written", "requirement_ids": ["REQ-001"],
                        "rows": [["path", "$HOME/$USER", "$HOME/$USER"]], "checked_at": None, "figure": None}],
        "figures": [], "output": {"lines": ["echo $x$"], "total_lines": 1, "truncated": False}, "jobs": [],
    }
    (results / "PY-ENTROPY" / "result.json").write_text(json.dumps(record))
    # A result.md record is read as Markdown, which must not read its dollars as maths either.
    (results / "PY-ENTROPY-MD").mkdir()
    (results / "PY-ENTROPY-MD" / "result.md").write_text(
        "# PY-ENTROPY-MD\n\n- Kind: `test`\n- Status: `passed`\n- Source: `test_entropy.py`\n"
        "- Description: Exports $LANG/$TERM before the run.\n\n## Output (last 20 lines)\n\n```text\necho $y$\n```\n"
    )
    return root, results


@pytest.mark.parametrize("math", [True, False])
def test_math_typesets_the_documents_and_never_the_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, math: bool
) -> None:
    import traceflow.pdf_generator as pdf_generator

    fake = _FakeLatex()
    monkeypatch.setattr(pdf_generator.subprocess, "check_output", fake)
    corpus, results = _corpus(tmp_path / "corpus", math=math)
    report = PdfReport(process_directory(str(corpus), version="1.0"), test_results_dir=str(results))
    report.render(include_design=False, include_supplementary=False, evidence_layout="record",
                  work_dir=str(tmp_path / "work"))
    tex = fake.tex
    formula = "H = L \\times \\log_2(N)"
    if math:
        assert f"\\({formula}\\)" in tex and f"\\[ {formula} \\]" in tex
    else:
        assert f"\\({formula}\\)" not in tex and "\\$H = L" in tex
    # The evidence record's dollars print as written either way.
    assert "\\$HOME/\\$USER" in tex and "\\$LANG/\\$TERM" in tex
    assert "\\(x\\)" not in tex and "\\(HOME" not in tex and "\\(LANG" not in tex
