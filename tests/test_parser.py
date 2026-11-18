from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from traceflow.parser import process_directory


def _write_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    normalised = textwrap.dedent(content).strip()
    path.write_text(normalised + "\n")


def test_process_directory_parses_risks_and_design(tmp_path: Path) -> None:
    requirements_path = Path(tmp_path) / "requirements" / "requirements.md"
    tests_path = Path(tmp_path) / "tests" / "tests.md"
    risks_path = Path(tmp_path) / "risks" / "risk-register.md"
    design_path = Path(tmp_path) / "design" / "design-overview.md"
    docs_path = Path(tmp_path) / "docs" / "ius.md"

    _write_markdown(
        requirements_path,
        """
        # Requirements

        ## REQ-001: Login

        The system shall authenticate clinicians.
        """,
    )

    _write_markdown(
        tests_path,
        """
        # Tests

        ## TEST-001: Login succeeds

        **Requirement ID:** REQ-001
        """,
    )

    _write_markdown(
        risks_path,
        """
        # Risk Register

        Intro paragraph.

        ## RISK-001: Incorrect association

        Hazardous Situation: Images are shown for the wrong patient

        Harm: Misdiagnosis

        Cause: Race condition during message ingestion

        Severity: High

        Probability: Medium

        Controls: Requirements REQ-001 verified by TEST-001

        Residual Severity: Medium

        Residual Probability: Low
        """,
    )

    _write_markdown(
        design_path,
        """
        # Design Overview

        ## Authentication

        Refers to REQ-001.
        """,
    )

    _write_markdown(
        docs_path,
        """
        # Installation Qualification

        Ensure TEST-001 evidence is archived.
        """,
    )

    document = process_directory(str(tmp_path), version="1.0.0")

    assert len(document.risks) == 1
    risk_doc = document.risks[0]
    assert risk_doc.title == "Risk Register"
    assert len(risk_doc.items) == 1
    risk = risk_doc.items[0]
    assert risk.risk_id == "RISK-001"
    assert risk.attributes["severity"] == "High"
    assert risk.attributes["probability"] == "Medium"
    assert risk.attributes["residual_severity"] == "Medium"
    assert sorted(risk.requirement_refs) == ["REQ-001"]
    assert sorted(risk.test_refs) == ["TEST-001"]

    assert len(document.design_documents) == 1
    assert document.design_documents[0].category == "design"
    assert len(document.supplementary_documents) == 1
    assert document.supplementary_documents[0].category == "general"


def test_risk_reference_validation(tmp_path: Path) -> None:
    requirements_path = Path(tmp_path) / "requirements" / "requirements.md"
    risks_path = Path(tmp_path) / "risks" / "risk-register.md"

    _write_markdown(
        requirements_path,
        """
        # Requirements

        ## REQ-100: Example

        Some body text.
        """,
    )

    _write_markdown(
        risks_path,
        """
        # Risk Register

        ## RISK-404: Missing control

        Controls: Mitigated by REQ-999
        """,
    )

    with pytest.raises(ValueError, match="Risk `RISK-404` references requirement `REQ-999`"):
        process_directory(str(tmp_path), version="0.1.0")


def test_underscores_and_backslashes_are_literal() -> None:
    """``_`` never starts emphasis, and a backslash escapes only Markdown's own characters."""
    from traceflow.parser import parse_markdown

    paragraph = parse_markdown(
        "_act with _ref, keeps its '._' name, \\_KEY \\*x, C:\\Users\\.cfg \\d *emph*\n"
    )[0]["children"]
    text = "".join(child.get("text", "") for child in paragraph if child["type"] == "text")
    assert "_act with _ref, keeps its '._' name, _KEY *x, C:\\Users\\.cfg \\d " == text
    assert [child["type"] for child in paragraph if child["type"] != "text"] == ["emphasis"]


# Markdown covering what TraceFlow reads differently from CommonMark, and its mistune 2 AST
# (adjacent text nodes merged). The mistune 3 port must keep returning exactly this.
LEGACY_MARKDOWN = (
    '##\u00a0REQ-001:\u00a0Upload a scan\n'
    '\n'
    'The _act and _ref files, `vf_metadata.json` and C:\\Users\\.x keep \\- and \\. but \\* is a star.\n'
    'A *starred* word, **strong** text, ~~gone~~ and a [link](https://example.com/a_b "T").\n'
    'Append ` (copy)` to the name, not _italic_.\n'
    '\n'
    '- item one\n'
    '  lazy line\n'
    '- item **two**\n'
    '  1. nested\n'
    '\n'
    '```manualtest\n'
    '```\n'
    '\n'
    '    indented code\n'
    '\n'
    '| Field | Value |\n'
    '|:------|------:|\n'
    '| a_b   | `x`   |\n'
)
LEGACY_AST = (
    [{'type': 'heading', 'children': [{'type': 'text', 'text': 'REQ-001:\xa0Upload a scan'}], 'level': 2},
     {'type': 'paragraph',
      'children': [{'type': 'text', 'text': 'The _act and _ref files, '},
                   {'type': 'codespan', 'text': 'vf_metadata.json'},
                   {'type': 'text', 'text': ' and C:\\Users\\.x keep \\- and \\. but * is a star.\nA '},
                   {'type': 'emphasis', 'children': [{'type': 'text', 'text': 'starred'}]},
                   {'type': 'text', 'text': ' word, '},
                   {'type': 'strong', 'children': [{'type': 'text', 'text': 'strong'}]},
                   {'type': 'text', 'text': ' text, '},
                   {'type': 'strikethrough', 'children': [{'type': 'text', 'text': 'gone'}]},
                   {'type': 'text', 'text': ' and a '},
                   {'type': 'link',
                    'link': 'https://example.com/a_b',
                    'children': [{'type': 'text', 'text': 'link'}],
                    'title': 'T'},
                   {'type': 'text', 'text': '.\nAppend '},
                   {'type': 'codespan', 'text': '(copy)'},
                   {'type': 'text', 'text': ' to the name, not _italic_.'}]},
     {'type': 'list',
      'children': [{'type': 'list_item',
                    'children': [{'type': 'block_text',
                                  'children': [{'type': 'text', 'text': 'item one\nlazy line'}]}],
                    'level': 1},
                   {'type': 'list_item',
                    'children': [{'type': 'block_text',
                                  'children': [{'type': 'text', 'text': 'item '},
                                               {'type': 'strong',
                                                'children': [{'type': 'text', 'text': 'two'}]}]},
                                 {'type': 'list',
                                  'children': [{'type': 'list_item',
                                                'children': [{'type': 'block_text',
                                                              'children': [{'type': 'text',
                                                                            'text': 'nested'}]}],
                                                'level': 2}],
                                  'ordered': True,
                                  'level': 2}],
                    'level': 1}],
      'ordered': False,
      'level': 1},
     {'type': 'block_code', 'text': '\n', 'info': 'manualtest'},
     {'type': 'block_code', 'text': 'indented code\n\n', 'info': None},
     {'type': 'table',
      'children': [{'type': 'table_head',
                    'children': [{'type': 'table_cell',
                                  'children': [{'type': 'text', 'text': 'Field'}],
                                  'align': 'left',
                                  'is_head': True},
                                 {'type': 'table_cell',
                                  'children': [{'type': 'text', 'text': 'Value'}],
                                  'align': 'right',
                                  'is_head': True}]},
                   {'type': 'table_body',
                    'children': [{'type': 'table_row',
                                  'children': [{'type': 'table_cell',
                                                'children': [{'type': 'text', 'text': 'a_b'}],
                                                'align': 'left',
                                                'is_head': False},
                                               {'type': 'table_cell',
                                                'children': [{'type': 'codespan', 'text': 'x'}],
                                                'align': 'right',
                                                'is_head': False}]}]}]}]
)


def test_parse_markdown_keeps_the_mistune_2_reading() -> None:
    from traceflow.parser import parse_markdown

    assert parse_markdown(LEGACY_MARKDOWN) == LEGACY_AST
