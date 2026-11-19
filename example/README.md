# TraceFlow example: DemoScan

DemoScan is a made-up MRI platform. Its documentation is in [`source/`](source) and its automated
test evidence in [`test-results/`](test-results); [`build.sh`](build.sh) builds the three PDFs here.

| PDF | Built with |
|---|---|
| [DemoScan-Validation-Pack.pdf](DemoScan-Validation-Pack.pdf) | the default layout: every source file, list traceability matrices, evidence records, manual-test forms, the risk register on A3 pages, and a cover with sign-off blocks |
| [DemoScan-Traceability-Report.pdf](DemoScan-Traceability-Report.pdf) | `--structure hierarchy`: release results and CI run register, a chapter per Tier 1 requirement, a risk trace, and a cover with no sign-off (`--cover-no-signoff`) |
| [DemoScan-Risk-Register.pdf](DemoScan-Risk-Register.pdf) | `--render-mode risks-only` |

## Where each part comes from

| File | What it shows |
|---|---|
| [`source/config.yml`](source/config.yml) | the product name, maths (`math: true`), the file order and the requirement hierarchy (Tier 1 prefix `REQ-T1-`) |
| [`source/requirements/tier1-requirements.md`](source/requirements/tier1-requirements.md) | Tier 1 requirements with their `IUS Source` |
| [`source/requirements/platform-requirements.md`](source/requirements/platform-requirements.md) | requirements with `Parent:` lines, plus maths, an image, a table, a code block and mermaid diagrams |
| [`source/tests/platform-tests.md`](source/tests/platform-tests.md) | tests with `manualtest` forms and `autotest` evidence, a test cover page, and Tier 1 journey tests |
| [`source/risks/risk-register.md`](source/risks/risk-register.md) | risks scored before and after their controls, each naming its requirement and tests |
| [`source/design/design-spec.md`](source/design/design-spec.md) | a design document with a mermaid architecture diagram |
| [`source/docs/ius.md`](source/docs/ius.md) | any other document, included as it is, with its IDs linked |
| [`test-results/`](test-results) | typed evidence records (`result.json`): assertions with check / expected / actual rows, and screenshots with their SHA-256 |

## Rebuilding

From the repository root:

    example/build.sh

It needs `pdflatex` and, for the diagrams, mermaid-cli (`mmdc`). If `mmdc` cannot find its Chrome,
set `PUPPETEER_EXECUTABLE_PATH` to an installed one. `build.sh` sets `SOURCE_DATE_EPOCH`, so an
unchanged document rebuilds to an identical file and only real changes show in git.
