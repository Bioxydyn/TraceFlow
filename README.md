# TraceFlow

![TraceFlow Logo](./traceflow/res/traceflow-logo.png)

TraceFlow is a Python library designed to help manage software development documentation and testing processes, streamlining the production of requirements, design documents, and manual test cases. With built-in support for PDF export, you can easily sign the output making TraceFlow particularly suited for projects in regulated environments or environments where compliance with 21 CFR 11 is necessary.

By leveraging TraceFlow, you can:

- Maintain your documentation in the same git repository as your code, ensuring version control and easy collaboration.
- Automatically produce a "validation pack" containing all requirements, design, and test plans in PDF format.
- Generate a traceability matrix linking all requirements to all tests.
- Capture structured risk registers with requirement/test cross-links and colour-coded pre/post-mitigation risk levels.
- Create fillable PDF forms for manual tests.
- The result will look something [like this](example.pdf)

## Getting Started

To use TraceFlow, organize your Markdown files in the following folder structure:

```
project/
  ├── requirements/
  │   ├── requirements.md
  │   └── ...
  ├── design/
  │   ├── design.md
  │   └── ...
  ├── risks/
  │   ├── risk-register.md
  │   └── ...
  └── tests/
      ├── test_plan.md
      └── ...
```
Create your Markdown files based on the provided examples:

 - [Requirements Document Example](#requirements)
 - [Test Plan Example with Manual Test](#test-plan)
 - [Design Document Example](#design-document)

### Running TraceFlow

With your Markdown files in place, you can run TraceFlow using the following command:

    traceflow path/to/project-docs 1.0.0 project.pdf

The second argument is the version string that will be shown on the report (change it to whatever release identifier you need).

Two optional arguments let you replace the logos that appear in the header (top-left) and footer (top-right):

- `--top-left-logo` (alias `--traceflow-logo`) points to the image shown in the top-left of every page (header).
- `--top-right-logo` (alias `--voxelflow-logo`) points to the image shown in the bottom-left of every page (footer).

If you do not pass these options, TraceFlow falls back to the packaged images in `traceflow/res/`.

    traceflow path/to/project-docs 1.0.0 project.pdf --top-left-logo assets/traceflow.png --top-right-logo assets/voxelflow.png

This command will generate a PDF for all of your documentation and test cases, as well as a validation pack and traceability matrix.

Append `--individual-pdfs` to also emit per-section PDFs alongside the combined validation pack. For example, running
`traceflow traceflow 1.0.0 1corelab-validation-pack.pdf --individual-pdfs` will produce the main PDF plus files like
`1corelab-validation-pack-design.pdf`, `1corelab-validation-pack-docs.pdf`, `1corelab-validation-pack-risks.pdf`,
`1corelab-validation-pack-requirements.pdf`, and `1corelab-validation-pack-tests.pdf` when those sections exist in your
source documents.

Append `--skip-front-page` to omit the cover-page `Version under test` table and both signature sections.
This is useful when those fields are completed in another system.

#### Cover, footer and reproducible builds

- `--cover-version TEXT` fills the cover's "Version under test" row, for example
  `--cover-version "VoxelFlow 3.0.5 (commit c4a3b477ecdb)"`; `--cover-engineer TEXT` fills
  "Engineer". Not given, a row stays blank to be filled in by hand (today's cover); given as `""`,
  the row is left out.
- `--commit SHA` (7-40 hex) names the product commit under test. The footer then reads
  `<code> | <name> <VERSION> (<commit12>) | TraceFlow <version>` instead of
  `<code> | Report produced using TraceFlow <version>`.
- `--date-format FMT` prints the generation date (title block and cover) with that strftime format,
  with English month names, for example `--date-format "%d %B %Y"` gives `11 November 2026`.
  Without it the date is LaTeX's `\today`, as before. The hierarchy report and evidence volumes
  use `%d %B %Y` and the release footer by default.
- With `SOURCE_DATE_EPOCH` set (seconds since 1970, for example the commit time), every date
  TraceFlow or pdfTeX prints or stamps comes from it (`FORCE_SOURCE_DATE=1` is passed to pdflatex),
  the PDF trailer ID is a hash of the LaTeX source, and no `PTEX.*` file names are written, so two
  builds of the same inputs are byte-identical, in any folder. Form-field names (manual tests) and
  mermaid file names are derived from the input in every build.

These apply in every mode, as fixes: a requirement's shells print as one breakable, linked
`Verified by: TEST-A, TEST-B (n evidence records)` line instead of one run-on `Test ID:` line per
shell; the Risk Register heading sits on the A3 page with its table, in `\small` type, with the Test
Evidence IDs one per line; and a manual test's Pass/Fail/Skip row and comments box always share a
page.

### Releasing a new version

1. Bump `version` in `pyproject.toml` and `traceflow/version.py`, then run `uv lock`.
2. Commit as `Release X.Y.Z`, tag `X.Y.Z`, and push the commit and tag to `master`.
3. Build the distribution: `uv build`.
4. Publish to PyPI using `uv publish` (the project's `TWINE_USERNAME`/`TWINE_PASSWORD` env vars work — `uv publish` accepts them as `--username`/`--password`):

        uv publish --username "$TWINE_USERNAME" --password "$TWINE_PASSWORD" dist/traceflow_rtm-X.Y.Z*

### Running the example

TraceFlow includes the `examples` directory with requirements, design, risks, and tests. To reproduce that example, run:

```
uv run traceflow examples 1.0.0 test.pdf --test-results-dir example-test-results
```

On macOS you may need to prefix the command with `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib` so the bundled `cairosvg` finds `libcairo`. This example writes `test.pdf` into the current directory and stores temporary LaTeX artifacts in `report/`.

### Using `autotest` blocks

You can embed test evidence captured outside TraceFlow by pointing to a test-results folder:

- Pass `--test-results-dir PATH` when running TraceFlow.
- In your markdown test plan, use:

```autotest
TEST-ID
```

TraceFlow resolves `TEST-ID` to `PATH/TEST-ID/result.md` and renders that markdown content into the PDF.
If `PATH/TEST-ID` or `result.md` is missing, TraceFlow raises an error.

Expected folder structure:

```
test-results/
  TEST-ID/
    result.md
    result.json      # optional typed record (schema 1)
    screenshots/
      screenshot-001.png
      ...
```

`traceflow.evidence.load_evidence_record(folder, id)` reads one result as a typed
`EvidenceRecord` (kind, status, workflow, run ID and URL, head SHA, source, duration,
description, assertions with requirement IDs and rows, figures with SHA-256 and size, output
lines). It uses `result.json` when present and maps `result.md` onto the same fields
otherwise. A `result.json` figure hash that does not match the file, a missing figure, or a
status that disagrees with `result.md` fails the load.

#### How evidence prints

Every option below is off by default; without them an `autotest` block prints as it always
has (the "legacy" block: Description, Assertions, the metadata bullets, then the result's
sections). Escaping and layout fixes apply in every mode: `~`, `\` and inline HTML such as
`<docId>` print literally, code spans are escaped and may break after `/ . _ : -`, linked IDs
are never hyphenated, figures are capped at 0.6 of the text height (so a tall capture keeps
its caption on the page), the assertion's source code and the collector's duplicate
`**Caption:**` line are no longer printed, a multi-line Description prints whole, and code
listings use small proportional type that wraps anywhere, marking each continuation.

- `--evidence-layout record` prints each record as: an `Evidence <id>` header with the status
  on the right (a label and named destination `ev:<id>`, and a level-3 PDF bookmark); one
  provenance line (kind | workflow | run, linked when it has a URL | duration | source); the
  description; the assertions, each with its rows as a check/expected/actual table and a link
  to its figure; the output, only when there is some; the figures, each caption ending in the
  first 12 hex characters of the image's SHA-256. Its sub-headings are unnumbered and never in
  the contents. A folder with only `result.json` always prints this way.
- `--evidence-bound-by` ends each record-layout record with the shells, requirements and
  Tier 1s that bind it.
- `--dedupe-evidence` prints each record in full once, at its home binding (the first shell
  whose requirements, or their parents, include one its assertions name; else the first
  binding), and a one-line pointer elsewhere: `Evidence <id> (Passed): full record under
  TEST-X, p. N`, followed by the captions that concern that shell.
- `--evidence-mode reference --evidence-index E01.evidence-index.json [--evidence-index ...]`
  prints one row per binding instead of the record: ID | status | kind | assertion captions |
  `<volume> p.<page>`. It loads the records (for kind and captions) but stages no files. An ID
  missing from every index, or whose status differs from the test results, fails the render.
- `--evidence-index-out PATH` writes `{id: {volume, page, status, kind, anchor, shell,
  figures}}` for every record printed in full, with pages read from the final pass's `.aux`
  (`volume` is the `--document-code`).
- `--toc-depth N` sets the contents depth (0-5) and unnumbers headings, so no number is glued to
  an ID; PDF bookmarks keep depth 3.
- `--work-dir DIR` builds in `DIR` (default `./report`) with a stable `traceflow.tex`; give each
  concurrent render its own.
- `--strict-references` fails the render if the final pass reports any undefined reference. An
  undefined reference to an evidence record (`ev:`) always fails.

#### Rendering an evidence volume

    traceflow path/to/project-docs 3.0.5 out/BXD-VF-019-E01.pdf \
        --render-mode evidence-volume --volume-manifest volumes.yml --volume BXD-VF-019-E01 \
        --test-results-dir results --work-dir build/E01 [--cover-version "VoxelFlow 3.0.5 (commit abc123def456)"]

The whole directory is parsed and checked, then the volume prints:

- a cover with no signature block: "Evidence volume n of N, part of <report code> <version>",
  the version under test, the Tier 1 scope, "m of n records in this volume passed" and
  "Approved as part of <report code> <version>";
- contents (depth 2 unless `--toc-depth` says otherwise; `--skip-toc` drops them);
- a one-page "How to read this volume";
- a record index: ID, status, binding shells, page;
- the body, by Tier 1 and then by shell heading (ID, title and the requirements it verifies;
  the shell text stays in the report). Each record homed in the volume prints once in the
  record layout with its "Bound by" list; every other binding prints a pointer, to a page in
  this volume or to the volume that holds the record. Pass the other volumes' indexes with
  `--evidence-index` to give those pointers a page too. REQ and TEST IDs print as bold text,
  since the volume does not contain their sections.

The volume's index is written to `--evidence-index-out`, or `<OUTPUT stem>.evidence-index.json`.
The render fails unless every home record printed exactly once and every `ev:` label was placed.
`--document-code` (default: the volume code), `--document-type` (default "Traceability
Evidence Volume") and `--report-title` (default "<name> <version>: Traceability Evidence Volume
E01: <title>") override the cover. Render the volumes first, then the report with
`--evidence-mode reference` and their indexes: references run one way, report to volume.

### Project configuration (`config.yml`)

An optional `config.yml` in the input directory can set:

```yaml
name: VoxelFlow
# Files are read in sorted path order (case-insensitive). Globs listed here come first,
# in this order; a glob that matches no Markdown file is an error.
order:
  - requirements/VoxelFlow Tier 1 Requirements.md
  - tests/VoxelFlow Tier 1 Tests.md
# Only H2 headings whose first word matches this become requirement/test/risk items; other
# H2 sections stay prose. This is the default.
item_id_pattern: '^(REQ|TEST|RISK)-[A-Z0-9-]+'
# Present => the requirement hierarchy is checked when the directory is parsed.
hierarchy:
  top_level_prefix: REQ-VF-T1-      # Tier 1 IDs
  require_single_parent: true       # every other requirement: exactly one Tier 1 parent
  procedural_files:                 # parentless requirement files (globs)
    - requirements/Procedural Controls.md
```

Requirements name their parent with a `Parent: REQ-...` line. With `hierarchy:` set, the
parse fails on an unknown parent, a self-parent, a cycle, a `Parent:` line naming no ID, a
Tier 1 with a parent, and (with `top_level_prefix`) a requirement that does not trace to a
Tier 1, or does not have exactly one Tier 1 parent when `require_single_parent` is set.

### Evidence volumes

A large traceability report can be issued with evidence volumes, each holding whole Tier 1s.
A volume manifest names them:

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
    homes:                          # optional overrides: evidence ID -> home shell (or null)
      PW-TEST-VOX-UPLOAD-016: TEST-VOX-UPLOAD-016
```

Each shell goes to the volume of its requirements' Tier 1, and each evidence record has one
home: a functional Tier 2 shell, else a shell whose requirements are all in a regulatory
family, else a Tier 1 journey shell; ties go to manifest order. Check a manifest with:

    traceflow path/to/project-docs --check-volumes volumes.yml

It parses the whole directory, prints the ID-to-volume map as JSON on stdout and exits 1 if
a Tier 1 is unassigned or in two volumes, a shell or requirement spans two volumes, a record
has no home, or an override is invalid. `tests/fixtures/voxelflow-volumes-10-tier1.yml` is an
example. From Python, `traceflow.volumes.plan_volumes(document, load_manifest(path))` returns
the same plan, with `layout(volume)` giving each volume's Tier 1 groups, shells, home records
and pointer records.

To render a volume, see [Rendering an evidence volume](#rendering-an-evidence-volume).

### The signed report: `--structure hierarchy`

Render the volumes first, then the report from their indexes:

    traceflow path/to/project-docs 3.0.5 out/BXD-VF-019.pdf --render-mode requirements-tests-risks \
        --structure hierarchy --volume-manifest volumes.yml \
        --evidence-mode reference --evidence-index out/BXD-VF-019-E01.evidence-index.json [... E06] \
        --test-results-dir results --document-set-file out/BXD-VF-019-document-set.json \
        --document-code BXD-VF-019 --cover-version "VoxelFlow 3.0.5 (commit c4a3b477ecdb)" \
        --commit c4a3b477ecdba56ea6b31148c56006bad3a972c9 --risk-register-ref BXD-VF-020 \
        --strict-references --work-dir build/report

`--structure hierarchy` (default `files`, one section per source file) lays the report out by the
requirement hierarchy. It needs a Tier 1 prefix: `hierarchy.top_level_prefix` in `config.yml`, or
a `--volume-manifest`, which also orders the chapters (manifest order) and gives each requirement
its evidence volume. The report holds:

- after the cover, the document-set table (with `--document-set-file`), then contents at depth 1
  (`--toc-depth` overrides; PDF bookmarks go three levels deep);
- **How to read this report**;
- **Release results**: the cover's `--cover-result`, counts of requirements, shells, bindings and
  records (passed and not), records per volume (from the evidence indexes), and the **CI run
  register**: each workflow run once, with its run ID linked to its URL, head commit (red when it
  is not `--commit`), record count and conclusion (the workflow record's status, else whether every
  record from the run passed); records with no CI run are grouped by workflow;
- **Intended use to Tier 1**, when Tier 1 requirements carry an `IUS Source: ...` line;
- one chapter per Tier 1: the statement and its IUS Source line, `Verified by` and `Risks` lines,
  the **derived requirements** table (Requirement | Title | Area | Shells | Evidence passed/bound |
  Risks | Volume; a requirement with no shell is a red row), the Tier 1 journey shells (shells that
  verify only Tier 1s), then each derived requirement with its Area, `Parent`, `Verified by` and
  `Risks` lines and statement, followed by the shells that verify it (each printed once, under the
  first requirement it verifies), with their evidence rows;
- **Procedural Controls** (requirements in `procedural_files`) and **Requirements outside the Tier 1
  hierarchy** (any that trace to no Tier 1, and shells that verify no requirement), when there are
  any; the render fails unless every requirement and shell is printed exactly once;
- **Risk trace** instead of the register: Risk | Controls | Requirements | Shells | Evidence |
  Volume (`--risk-register-ref` names where the full register is);
- **Appendix A: Requirements by area**: each requirement file with its introduction and its
  requirements (Tier 1, page), then the test files' introductions;
- **Appendix B: Evidence index**: every bound record, its status, volume and page (inline evidence
  cites this report's page).

The hierarchy always prints list matrices. In the `files` structure, `--matrix-style list` replaces
each file's tick matrix with a portrait longtable (Requirement | Title | Parent | Verified by |
Evidence passed/bound | Risks) that repeats its header across pages; a requirement with no shell
is a red row, and shells that bind no record show `N/A (manual)` or `N/A (procedural)`.

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

### Requirements
```
# Requirements

## REQ-001: User authentication

Users must be able to authenticate using their email address and a password.

### Example LaTeX equation

The strength of the user's password should follow the entropy equation:

$$ H = L \times \log_2(N) $$

Where $H$ is the entropy, $L$ is the password length, and $N$ is the number of possible symbols.

## REQ-002: MRI dataset import

The platform must support importing MRI datasets in DICOM format.

### Example image (.png)

![Caption for example image](./mri_sample.png)

## REQ-003: Image analysis pipeline

- The platform should provide a Python API for building image analysis pipelines.
- The pipelines must be able to process MRI datasets in a compliant way.

### Example table

| Step          | Description                                      | API Function       |
|---------------|--------------------------------------------------|--------------------|
| Preprocessing | Remove noise, artifacts, and normalize intensity | preprocess_data()  |
| Segmentation  | Segment the relevant regions of interest         | segment_roi()      |
| Feature extraction | Extract features from segmented regions      | extract_features() |
| Classification | Classify the extracted features                 | classify_data()    |

### Example flow chart (using mermaid)

\```mermaid
%% caption: Example analysis pipeline
graph LR
A[Preprocessing] --> B[Segmentation]
B --> C[Feature extraction]
C --> D[Classification]
\```

```

### Test Plan
```
## TEST-001: User authentication

**Requirement ID:** REQ-001

### Test Steps:

1. Navigate to the login page.
2. Enter a valid email address and password.
3. Click the "Login" button.

### Expected Result:

The user is logged in and redirected to the main dashboard.

### Test Result (Manual Sign-off):

\```manualtest
\```

### Test Result (Automatic Evidence):

\```autotest
TEST-002
\```
```

### Design Document
```
## User Authentication

To address **REQ-001**, we will implement an authentication system using JWT (JSON Web Tokens). The system will include the following components:

- A login page with input fields for email and password
- A backend API endpoint to validate user credentials
- Middleware to validate JWT tokens for accessing protected resources

## MRI Dataset Import

To address **REQ-002**, we will develop a module to import MRI datasets in DICOM format. The module will include:

- A function to parse DICOM files
- Error handling for unsupported or malformed files
- Integration with the existing data storage system

## Image Analysis Pipeline

To address **REQ-003**, we will create a Python API for building and executing image analysis pipelines. This API will include:

- A set of Python classes to represent pipeline components
- Functions to connect and execute pipeline components
- Compliance checks to ensure the pipeline adheres to the required standards
```

### Risk Register Example
TraceFlow now understands risk registers and renders them on A3 landscape pages with colour-coded severity, probability, and residual risk ratings.

```
# Risk Register

## RISK-001: Incorrect study-patient association

Hazardous Situation: Clinician views wrong patient's images believing they are correct
Harm: Misdiagnosis, inappropriate treatment
Cause: Race condition during HL7/DICOM message processing leading to incorrect PatientID or AccessionNumber assignment
Severity: High
Probability: Medium
Controls: Unit/integration tests for identifier logic (REQ-005, TEST-002)
Residual Severity: Medium
Residual Probability: Low
Residual Risk: Operator review plus automatic quarantine when mismatches occur
```

### Generic Document Example
Any additional Markdown files (e.g., Installation/User Specifications) are included verbatim and can link to requirements, tests, or risks using their IDs:

```
# Installation & User Specification

- Execute TEST-003 and record the release tag.
- Confirm the identifier reconciliation feature (REQ-005) is active before go-live.
- Review RISK-001 and RISK-002 residual risk statements with the clinical safety officer.
```
