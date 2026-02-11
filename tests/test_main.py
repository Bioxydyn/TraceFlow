from __future__ import annotations

from pathlib import Path
from typing import Any

import traceflow.__main__ as traceflow_main


class _StubReport:
    render_kwargs: dict[str, Any] = {}

    def __init__(self, document: Any, **kwargs: Any):
        self.document = document
        self.init_kwargs = kwargs

    def render(self, **kwargs: Any) -> bytes:
        _StubReport.render_kwargs = kwargs
        return b"%PDF-FAKE"


def _patch_run_dependencies(monkeypatch: Any) -> None:
    monkeypatch.setattr(traceflow_main, "process_directory", lambda directory, version: object())
    monkeypatch.setattr(traceflow_main, "PdfReport", _StubReport)


def test_run_includes_cover_page_by_default(monkeypatch: Any, tmp_path: Path) -> None:
    _patch_run_dependencies(monkeypatch)
    output_path = tmp_path / "default.pdf"

    rc = traceflow_main._run(str(tmp_path), "1.0.0", str(output_path))

    assert rc == 0
    assert output_path.read_bytes() == b"%PDF-FAKE"
    assert _StubReport.render_kwargs["include_cover_page"] is True


def test_run_skip_front_page_disables_cover_page(monkeypatch: Any, tmp_path: Path) -> None:
    _patch_run_dependencies(monkeypatch)
    output_path = tmp_path / "skip-front-page.pdf"

    rc = traceflow_main._run(str(tmp_path), "1.0.0", str(output_path), skip_front_page=True)

    assert rc == 0
    assert output_path.read_bytes() == b"%PDF-FAKE"
    assert _StubReport.render_kwargs["include_cover_page"] is False
