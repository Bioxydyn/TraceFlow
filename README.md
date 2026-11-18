# TraceFlow

![TraceFlow](https://raw.githubusercontent.com/Bioxydyn/TraceFlow/master/traceflow/res/traceflow-logo.png)

TraceFlow turns requirements, tests, risks and design documents written in Markdown, kept in the
same git repository as your code, into a signed-off validation pack in PDF. It links every
requirement to the tests that verify it and the risks it controls, prints automated test evidence
and fillable manual-test forms, and builds reproducibly, for teams working to 21 CFR Part 11,
EU GMP Annex 11 or similar.

## See an example

DemoScan, a made-up MRI platform, built from
[`example/source/`](https://github.com/Bioxydyn/TraceFlow/tree/master/example/source) (Markdown) and
[`example/test-results/`](https://github.com/Bioxydyn/TraceFlow/tree/master/example/test-results)
(automated test evidence) by
[`example/build.sh`](https://github.com/Bioxydyn/TraceFlow/blob/master/example/build.sh):

| Document | What it shows |
|---|---|
| [**Validation Pack**](https://github.com/Bioxydyn/TraceFlow/blob/master/example/DemoScan-Validation-Pack.pdf) | everything in one PDF: design, requirements with traceability matrices, the test plan with evidence records and fillable forms, the risk register |
| [**Traceability Report**](https://github.com/Bioxydyn/TraceFlow/blob/master/example/DemoScan-Traceability-Report.pdf) | laid out by the requirement hierarchy: release results, CI run register, a chapter per Tier 1 requirement, risk trace, indexes |
| [**Risk Register**](https://github.com/Bioxydyn/TraceFlow/blob/master/example/DemoScan-Risk-Register.pdf) | the risk register on its own, scored before and after controls |

[`example/README.md`](https://github.com/Bioxydyn/TraceFlow/blob/master/example/README.md) says
which source file each part comes from.

## Install

    pip install traceflow-rtm

TraceFlow needs Python 3.14 and `pdflatex` (TeX Live, or MacTeX on macOS). Mermaid diagrams also
need [mermaid-cli](https://github.com/mermaid-js/mermaid-cli) (`mmdc`) on the `PATH`.

## Quick start

Put your Markdown in a folder like this:

```
docs/
  requirements/   REQ-... items
  tests/          TEST-... items, with manualtest and autotest blocks
  risks/          RISK-... items
  design/         design documents
  config.yml      optional: product name, file order, requirement hierarchy
```

Any other Markdown file is included as it is. Then run:

    traceflow docs 1.0.0 validation-pack.pdf

`1.0.0` is the version printed on the report. Add `--test-results-dir test-results` to print
automated test evidence. Copy [`example/source/`](https://github.com/Bioxydyn/TraceFlow/tree/master/example/source)
to start from a working set.

## Writing the documents

Each `##` heading that starts with an ID is an item; an ID mentioned anywhere links to it.

```markdown
## REQ-001: User authentication

Parent: REQ-T1-001

Users must sign in with their email address and a password.
```

```markdown
## TEST-001: User authentication

**Requirement ID:** REQ-001

1. Navigate to the login page and sign in.
```

A test's `manualtest` code block prints a fillable Pass / Fail / Skip form, and an `autotest` block
naming an evidence ID prints that record from `--test-results-dir`. A risk lists `Hazardous
Situation`, `Harm`, `Cause`, `Severity`, `Probability`, `Controls` (naming the requirements that
implement them), `Test evidence` and the residual scores. Set
`math: true` in `config.yml` to typeset `$...$` and `$$...$$` as LaTeX maths. The
[reference](https://github.com/Bioxydyn/TraceFlow/blob/master/docs/reference.md#markdown-formats)
has every format, and the example source uses each one.

## Common options

| Option | Effect |
|---|---|
| `--test-results-dir DIR` | where `autotest` evidence records are read from |
| `--evidence-layout record` | prints each evidence record with its assertions, figures and SHA-256s |
| `--matrix-style list` | list traceability matrices, with untested requirements in red |
| `--structure hierarchy` | the traceability report layout, one chapter per Tier 1 requirement |
| `--render-mode risks-only` | the risk register alone (also `requirements-tests-risks`) |
| `--cover-version TEXT`, `--cover-result TEXT`, `--cover-test-date TEXT` | fill the cover table |
| `--cover-no-signoff` | no signature blocks, for documents approved in another system |
| `--commit SHA` | names the commit under test in every footer |
| `--top-left-logo PATH`, `--top-right-logo PATH` | the header logos |

Set `SOURCE_DATE_EPOCH` to make builds byte-identical. The
[reference](https://github.com/Bioxydyn/TraceFlow/blob/master/docs/reference.md) describes every
option, including evidence volumes for very large reports.

## Development

    uv sync --all-groups
    uv run pytest tests
    uv run bash py-lint.sh
    uv run ty check traceflow tests

After a change that affects the output, rebuild the example with `example/build.sh` and commit the
PDFs. The build is reproducible, so an unchanged document gives an identical file.

To release: bump `version` in `pyproject.toml` and `traceflow/version.py`, run `uv lock`, commit as
`Release X.Y.Z`, tag `X.Y.Z` and push both to `master`. Then build and publish:

    uv build
    uv publish --username "$TWINE_USERNAME" --password "$TWINE_PASSWORD" dist/traceflow_rtm-X.Y.Z*
