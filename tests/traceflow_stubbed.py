"""The traceflow CLI with pdflatex and mmdc stubbed out, for tests that run example/build.sh.

Run as ``python -m tests.traceflow_stubbed ARGS...``. Each render keeps its LaTeX source in its
``--work-dir`` as ``traceflow.tex``; the PDFs it writes are placeholders.
"""
from __future__ import annotations

import subprocess  # noqa: S404
import sys
from typing import Any

import traceflow.__main__ as traceflow_main
import traceflow.pdf_generator as pdf_generator
from tests.test_evidence_render import _FakeLatex

_real_run = subprocess.run


def _fake_run(command: list[str], *args: Any, **kwargs: Any) -> subprocess.CompletedProcess:  # noqa: ANN401
    """Stands in for mermaid-cli: writes the file named after ``-o``."""
    if command and command[0] == "mmdc":
        with open(command[command.index("-o") + 1], "wb") as handle:
            handle.write(b"%PDF-FAKE")
        return subprocess.CompletedProcess(command, 0)
    return _real_run(command, *args, **kwargs)


def main() -> int:
    setattr(pdf_generator.subprocess, "check_output", _FakeLatex())  # noqa: B010 - a stub for pdflatex
    setattr(pdf_generator.subprocess, "run", _fake_run)  # noqa: B010 - a stub for mmdc
    sys.argv[0] = "traceflow"
    return traceflow_main.main()


if __name__ == "__main__":
    raise SystemExit(main())
