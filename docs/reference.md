# TraceFlow reference

The [README](../README.md) covers getting started. This page describes every input format and
option. [`example/`](../example) uses most of them, and [`example/build.sh`](../example/build.sh)
shows the commands.

- [Command line](#command-line)
- [Project configuration (`config.yml`)](#project-configuration-configyml)
- [Markdown formats](#markdown-formats)
- [Test evidence](#test-evidence)
- [The traceability report: `--structure hierarchy`](#the-traceability-report---structure-hierarchy)
- [Evidence volumes](#evidence-volumes)

## Command line

    traceflow DIRECTORY VERSION OUTPUT [options]

`VERSION` is printed on the report. `traceflow --help` lists every option.

### What to render

- `--render-mode` is `all` (default: every section), `requirements-tests-risks`, `risks-only` or
  `evidence-volume` (see [Evidence volumes](#evidence-volumes)).
- `--structure files` (default) prints one section per source file;
  `--structure hierarchy` lays the report out by the requirement hierarchy (see
  [below](#the-traceability-report---structure-hierarchy)).
- `--matrix-style list` replaces each requirements file's tick matrix with a portrait table
  (Requirement | Title | Parent | Verified by | Evidence passed/bound | Risks) that repeats its
  header across pages. A requirement with no test is a red row, and a test that binds no evidence
  shows `N/A (manual)` or `N/A (procedural)`. The hierarchy always prints list matrices.
- `--traceability-a3` prints the traceability matrices on A3 landscape pages.
- `--individual-pdfs` also writes one PDF per section next to OUTPUT (for example
  `pack-requirements.pdf`, `pack-tests.pdf`, `pack-risks.pdf`).
- `--skip-toc` drops the contents; `--toc-depth N` sets its depth (0-5) and unnumbers headings,
  so no number is glued to an ID (PDF bookmarks keep depth 3).

### Title, header and footer

- `--report-title`, `--document-code` and `--document-type` fill the title and the document table.
- `--top-left-logo` (alias `--traceflow-logo`) and `--top-right-logo` (alias `--voxelflow-logo`)
  replace the header logos; `--hide-right-logo` leaves the right-hand slot empty. Without them the
  bundled logos in `traceflow/res/` are used.
- `--commit SHA` (7-40 hex) names the product commit under test. The footer then reads
  `<code> | <name> <VERSION> (<commit12>) | TraceFlow <version>` instead of
  `<code> | Report produced using TraceFlow <version>`.
- `--date-format FMT` prints the generation date with that strftime format and English month
  names: `--date-format "%d %B %Y"` gives `01 January 2026`. Without it the date is LaTeX's
  `\today`. The hierarchy report and evidence volumes default to `%d %B %Y`.

### The cover

The cover has a table (version under test, engineer, tester, test date, generation date, result,
approved usage) and, by default, two sign-off blocks: "Manual tests carried out by" and
"Validation pack result approved by".

| Option | Effect |
|---|---|
| `--cover-version TEXT` | fills "Version under test"; `""` leaves the row out |
| `--cover-engineer TEXT` | fills "Engineer"; `""` leaves the row out |
| `--cover-no-tester` | leaves out the "Tester" row (fully automated packs) |
| `--cover-test-date TEXT`, `--cover-result TEXT`, `--cover-approved-usage TEXT` | fill those cells (blank by default) |
| `--cover-single-signoff` | prints only the "Validation pack result approved by" block |
| `--cover-signature-boxes` | prints two boxed signature placeholders instead of the blocks |
| `--cover-no-signoff` | keeps the table and prints no sign-off, for a document approved in another system |
| `--skip-front-page` | leaves out the table and the sign-off |

A row given no value stays blank, to be filled in by hand. `--cover-no-signoff` cannot be combined
with `--cover-single-signoff` or `--cover-signature-boxes`.

### Building

- `--work-dir DIR` builds in `DIR` (default `./report`) with a stable `traceflow.tex`; give each
  concurrent render its own.
- `--strict-references` fails the render if the final pass reports any undefined reference. An
  undefined reference to an evidence record (`ev:`) always fails.
- With `SOURCE_DATE_EPOCH` set (seconds since 1970, for example the commit time), every date
  TraceFlow or pdfTeX prints or stamps comes from it (`FORCE_SOURCE_DATE=1` is passed to pdflatex),
  the PDF trailer ID is a hash of the LaTeX source, and no `PTEX.*` file names are written, so two
  builds of the same inputs are byte-identical, in any folder. Form-field names (manual tests) and
  mermaid file names are derived from the input in every build.

## Project configuration (`config.yml`)

An optional `config.yml` in the input directory can set:

```yaml
name: DemoScan                  # the product name used in titles and footers
math: true                      # read $...$ and $$...$$ as maths (see Maths below); default false
# Files are read in sorted path order (case-insensitive). Globs listed here come first,
# in this order; a glob that matches no Markdown file is an error.
order:
  - requirements/tier1-requirements.md
  - requirements/platform-requirements.md
# Only H2 headings whose first word matches this become requirement/test/risk items; other
# H2 sections stay prose. This is the default.
item_id_pattern: '^(REQ|TEST|RISK)-[A-Z0-9-]+'
# Present => the requirement hierarchy is checked when the directory is parsed.
hierarchy:
  top_level_prefix: REQ-T1-     # Tier 1 IDs
  require_single_parent: true   # every other requirement: exactly one Tier 1 parent
  procedural_files:             # parentless requirement files (globs)
    - requirements/Procedural Controls.md
```

Requirements name their parent with a `Parent: REQ-...` line. With `hierarchy:` set, the parse
fails on an unknown parent, a self-parent, a cycle, a `Parent:` line naming no ID, a Tier 1 with a
parent, and (with `top_level_prefix`) a requirement that does not trace to a Tier 1, or does not
have exactly one Tier 1 parent when `require_single_parent` is set.

## Markdown formats

A file's top-level folder, or else its file name, decides what it holds: a name starting with `req`
is requirements, `test` tests, `risk` or `hazard` risks, and `design` design. Any other Markdown is
included as it is. Each `##` heading that starts with an ID is an item, and an ID mentioned anywhere
links to its item.

### Requirements

```markdown
## REQ-001: User authentication

Parent: REQ-T1-001

Users must sign in with their email address and a password before they can see any study.
```

A Tier 1 requirement can carry an `IUS Source: ...` line, which the traceability report prints in
its "Intended use to Tier 1" table. Sub-headings, lists, tables, images, code blocks and mermaid
diagrams (a `mermaid` code block, optionally starting with `%% caption: ...`) are typeset. TraceFlow
reads Markdown as text: `_` never starts emphasis, and a backslash only escapes Markdown's own
characters, so file names, keys and paths print as written.

### Maths

Maths is off unless `config.yml` sets `math: true`. Without it a `$` prints as written, as it has
since 0.0.31, so prices, shell variables and DICOM values are never read as maths.

With `math: true`, `$$...$$` is display maths and `$...$` inline maths, in LaTeX:

```markdown
A password needs $H \geq 60$ bits, where

$$ H = L \times \log_2(N) $$
```

- Inline maths follows pandoc's rule: the opening `$` must be followed by a non-space, and the
  closing `$` must follow a non-space and must not be followed by a digit. So `$20 and $30` and
  `$HOME and $PATH` stay text, but `$HOME/$USER` would be read as maths: write `\$HOME/\$USER`.
- `\$` is a literal dollar, inside maths and out.
- Maths is typeset in paragraphs, list items and table cells. In a heading, emphasis or a risk
  register field it prints as written.
- Code spans and code blocks are never maths, and test evidence is never read as maths.
- A formula with unbalanced braces fails the parse, naming the formula.

### Tests

````markdown
## TEST-001: User authentication

**Requirement ID:** REQ-001

### Test Steps:

1. Navigate to the login page.

### Expected Result:

The user is logged in.

```manualtest
```

```autotest
E2E-LOGIN-001
```
````

- A `manualtest` block prints a fillable form: Pass / Fail / Skip and a comments box.
  `--manualtest-comment-height` sets the box's height (default `1.2cm`).
- An `autotest` block prints the named evidence record (see [Test evidence](#test-evidence)).
- A `testcoverpage` block prints a test-session cover (tester, test date, result, observations).
- A test that names only Tier 1 requirements is a Tier 1 journey test.

### Risks

```markdown
## RISK-001: Incorrect study-patient association

Hazardous Situation: A clinician views the wrong patient's images.
Harm: Misdiagnosis or inappropriate treatment.
Cause: A race condition assigns the wrong PatientID.
Severity: High
Probability: Medium
Controls: Identifier reconciliation quarantines mismatched studies (REQ-005).
Test evidence: TEST-002.
Residual Severity: Medium
Residual Probability: Low
Residual Risk: Acceptable: an operator reviews every quarantined study.
```

The register prints on A3 landscape pages, with the scoring method and colour-coded ratings.
Severity and probability are scored Low (1), Medium (3), High (4) or Very High (5), or with the
other words in the printed scoring table (Minor, Rare, Moderate, Major, Critical and so on). The requirement
and test IDs a risk mentions (in `Controls`, `Test evidence`, `Linked requirements` or
`Linked tests`) link it to them. Other field names accepted: `Hazard`, `Risk estimate`,
`Risk control(s)`, `Risk control measure`, `Residual risk assessment` and `Detection`.

## Test evidence

Pass `--test-results-dir PATH`. An `autotest` block naming `ID` prints `PATH/ID/`:

```
test-results/
  ID/
    result.json      # the typed record (schema 1); or result.md
    screenshots/
      screenshot-001.png
```

`result.json` holds the kind, status, workflow, run ID and URL, head commit, source, duration,
description, assertions (caption, requirement IDs, check / expected / actual rows, figure) and
figures (path, caption, SHA-256, size). See `example/test-results/` for complete records.
`traceflow.evidence.load_evidence_record(folder, id)` reads either file as a typed
`EvidenceRecord`. A figure whose hash does not match the file, a missing figure, or a status that
disagrees between `result.json` and `result.md` fails the load. A missing record fails the render.

### How evidence prints

Every option below is off by default; without them an `autotest` block prints the "legacy" block:
Description, Assertions, the metadata bullets, then the result's sections. A folder with only
`result.json` always prints in the record layout.

- `--evidence-layout record` prints each record as: an `Evidence <id>` header with the status on
  the right (a label and named destination `ev:<id>`, and a level-3 PDF bookmark); one provenance
  line (kind | workflow | run, linked when it has a URL | duration | source); the description; the
  assertions, each with its rows as a check/expected/actual table and a link to its figure; the
  output, only when there is some; the figures, each caption ending in the first 12 hex
  characters of the image's SHA-256.
- `--evidence-bound-by` ends each record-layout record with the tests, requirements and Tier 1s
  that bind it.
- `--dedupe-evidence` prints each record in full once, at its home binding (the first test whose
  requirements, or their parents, include one its assertions name; else the first binding), and a
  one-line pointer elsewhere.
- `--evidence-mode reference --evidence-index E01.evidence-index.json [--evidence-index ...]`
  prints one row per binding instead of the record: ID | status | kind | assertion captions |
  `<volume> p.<page>`. An ID missing from every index, or whose status differs from the test
  results, fails the render.
- `--evidence-index-out PATH` writes `{id: {volume, page, status, kind, anchor, shell, figures}}`
  for every record printed in full, with pages read from the final pass's `.aux` (`volume` is the
  `--document-code`).

## The traceability report: `--structure hierarchy`

`--structure hierarchy` lays the report out by the requirement hierarchy. It needs a Tier 1 prefix:
`hierarchy.top_level_prefix` in `config.yml`, or a `--volume-manifest`, which also orders the
chapters and gives each requirement its evidence volume. The report holds:

- after the cover, the document-set table (with `--document-set-file`), then the contents;
- **How to read this report**;
- **Release results**: the cover's result, counts of requirements, tests, bindings and records,
  records per volume, and the **CI run register**: each workflow run once, with its run ID linked
  to its URL, head commit (red when it is not `--commit`), record count and conclusion;
- **Intended use to Tier 1**, when Tier 1 requirements carry an `IUS Source: ...` line;
- one chapter per Tier 1: its statement, the derived requirements table (a requirement with no
  test is a red row), its journey tests, then each derived requirement with the tests that verify
  it and their evidence;
- **Procedural Controls** and **Requirements outside the Tier 1 hierarchy**, when there are any;
  the render fails unless every requirement and test is printed exactly once;
- **Risk trace** instead of the register: Risk | Controls | Requirements | Tests | Evidence |
  Volume (`--risk-register-ref` names where the full register is);
- **Appendix A: Requirement index** and **Appendix B: Evidence index**.

`--document-set-file` is JSON, either a list of entries or
`{"documents": [...], "commit": "...", "version": "...", "report": "..."}`. Each entry:

```json
{"code": "BXD-VF-019-E01", "title": "Traceability Evidence Volume E01: Data Ingestion",
 "tier1": ["REQ-VF-T1-001", "REQ-VF-T1-002"], "pages": 97, "records": 112,
 "sha256": "<64 lower-case hex>", "file": "BXD-VF-019-E01.pdf", "commit": "<sha>", "size_bytes": 7239089}
```

`code`, `title`, `sha256`, `pages` and `records` are required; `tier1_scope` (text) may stand in for
`tier1`; other keys are refused. The render fails if the entries are not exactly the manifest's
volumes, if an entry's `records` differs from the evidence indexes, if a commit differs from
`--commit`, or if the report lists itself. The cover then says that its approval covers every
document in the table.

## Evidence volumes

A large traceability report can be issued with evidence volumes, each holding whole Tier 1s. A
volume manifest names them:

```yaml
structure_revision: 1
report: {code: BXD-VF-019, title: Requirements, Tests and Risk Traceability Report}
tier1_prefix: REQ-VF-T1-          # optional when config.yml sets hierarchy.top_level_prefix
require_single_parent: true       # optional
procedural_files: []              # optional, merged with config.yml's
regulatory_families: [REQ-VOX-21CFR11-*, REQ-VOX-ANNEX11-*]   # the default
volumes:
  - code: BXD-VF-019-E01
    slug: ingestion
    title: Data Ingestion, De-identification and Upload Portals
    audience: Sponsor data managers
    tier1: [REQ-VF-T1-001, REQ-VF-T1-002]
    max_pages: 150
    max_mb: 10
    homes:                          # optional overrides: evidence ID -> home test (or null)
      PW-TEST-VOX-UPLOAD-016: TEST-VOX-UPLOAD-016
```

Each test goes to the volume of its requirements' Tier 1, and each evidence record has one home: a
functional Tier 2 test, else a test whose requirements are all in a regulatory family, else a
Tier 1 journey test; ties go to manifest order. Check a manifest with:

    traceflow path/to/project-docs --check-volumes volumes.yml

It prints the ID-to-volume map as JSON and exits 1 if a Tier 1 is unassigned or in two volumes, a
test or requirement spans two volumes, a record has no home, or an override is invalid.
`tests/fixtures/voxelflow-volumes-10-tier1.yml` is an example.

Render the volumes first, then the report from their indexes; references run one way, report to
volume:

    traceflow docs 3.0.5 out/BXD-VF-019-E01.pdf --render-mode evidence-volume \
        --volume-manifest volumes.yml --volume BXD-VF-019-E01 \
        --test-results-dir results --work-dir build/E01

    traceflow docs 3.0.5 out/BXD-VF-019.pdf --render-mode requirements-tests-risks \
        --structure hierarchy --volume-manifest volumes.yml \
        --evidence-mode reference --evidence-index out/BXD-VF-019-E01.evidence-index.json [...] \
        --test-results-dir results --document-set-file out/BXD-VF-019-document-set.json \
        --document-code BXD-VF-019 --commit <sha> --risk-register-ref BXD-VF-020 \
        --strict-references --work-dir build/report

A volume prints a cover with no signature block ("Evidence volume n of N, part of <report>"), the
version under test, the Tier 1 scope and the volume's result; contents; a one-page "How to read
this volume"; a record index; and the body, by Tier 1 and then by test. Each record homed in the
volume prints once in the record layout; every other binding prints a pointer. Pass the other
volumes' indexes with `--evidence-index` to give those pointers a page. The volume's index is
written to `--evidence-index-out`, or `<OUTPUT stem>.evidence-index.json`. `--document-code` must
be the volume's code from the manifest.
