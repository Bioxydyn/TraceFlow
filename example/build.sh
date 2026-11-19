#!/usr/bin/env bash
# Rebuild the example documents in this folder from source/ and test-results/.
#
#   example/build.sh                      # with this checkout (uv run)
#   TRACEFLOW=traceflow example/build.sh  # with an installed traceflow
#   OUT=/tmp/demo example/build.sh        # write the PDFs somewhere else
#
# Needs pdflatex (TeX Live) and, for the mermaid diagrams, mmdc (@mermaid-js/mermaid-cli).
# SOURCE_DATE_EPOCH fixes every date, so the same inputs give byte-identical PDFs.
set -euo pipefail

cd "$(dirname "$0")"
TRACEFLOW="${TRACEFLOW:-uv run --project .. traceflow}"
OUT="${OUT:-.}"
export SOURCE_DATE_EPOCH=1767225600  # 1 January 2026, 00:00 UTC

VERSION=1.0.0
COMMIT=0a1b2c3d4e5f60718293a4b5c6d7e8f901234567  # the (made-up) commit the evidence is for
COVER="DemoScan $VERSION (commit ${COMMIT:0:12})"
DATE="01 January 2026"
COMMON=(--commit "$COMMIT" --date-format "%d %B %Y" --top-right-logo "$PWD/demoscan-logo.png")

# 1. The validation pack: every source file in one PDF, with the evidence records, a list
#    traceability matrix per requirements file, the risk register on A3 pages and fillable
#    manual-test forms. The cover keeps its sign-off blocks for a wet signature.
$TRACEFLOW source "$VERSION" "$OUT/DemoScan-Validation-Pack.pdf" --test-results-dir test-results \
    --document-code DEMO-VAL --document-type "Validation Pack" \
    --cover-version "$COVER" --cover-test-date "$DATE" --cover-result "Pass" \
    --evidence-layout record --evidence-bound-by --matrix-style list \
    "${COMMON[@]}" --work-dir "$OUT/build/validation-pack"

# 2. The traceability report: laid out by the requirement hierarchy (one chapter per Tier 1
#    requirement), with release results, the CI run register, a risk trace and indexes. It is
#    approved in a separate system, so its cover has no sign-off.
$TRACEFLOW source "$VERSION" "$OUT/DemoScan-Traceability-Report.pdf" --test-results-dir test-results \
    --render-mode requirements-tests-risks --structure hierarchy \
    --document-code DEMO-RTM --document-type "Requirements, Tests and Risk Traceability Report" \
    --report-title "DemoScan $VERSION: Requirements, Tests and Risk Traceability Report" \
    --cover-version "$COVER" --cover-engineer "" --cover-no-tester --cover-test-date "$DATE" \
    --cover-no-signoff --evidence-layout record --evidence-bound-by \
    --risk-register-ref DEMO-RISK --strict-references \
    "${COMMON[@]}" --work-dir "$OUT/build/traceability-report"

# 3. The risk register on its own.
$TRACEFLOW source "$VERSION" "$OUT/DemoScan-Risk-Register.pdf" --render-mode risks-only \
    --document-code DEMO-RISK --document-type "Risk Register" \
    --report-title "DemoScan $VERSION: Risk Register" --skip-toc --skip-front-page \
    "${COMMON[@]}" --work-dir "$OUT/build/risk-register"

echo "Built DemoScan-Validation-Pack.pdf, DemoScan-Traceability-Report.pdf and DemoScan-Risk-Register.pdf in $OUT"
