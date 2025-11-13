#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 3 ]; then
    echo "Usage: ./run-test-video.sh \"test name regex\" output.webm output.txt"
    exit 1
fi

TEST_NAME="$1"
OUTPUT_VIDEO="$2"
OUTPUT_TEXT="$3"

# Ensure clean test-results
if [ -d test-results ]; then
    echo "▶ Removing old test-results folder..."
    rm -rf test-results
fi

echo "▶ Running test: $TEST_NAME"
echo "▶ Video will be saved as: $OUTPUT_VIDEO"
echo "▶ Text output will be saved as: $OUTPUT_TEXT"

# Temporary file to capture test output
TMP_OUTPUT=$(mktemp)

# Run Playwright, capturing output while also displaying it live
# tee duplicates output: both to terminal AND TMP_OUTPUT
set +e
npx playwright test -g "$TEST_NAME" 2>&1 | tee "$TMP_OUTPUT"
TEST_EXIT_CODE=${PIPESTATUS[0]}
set -e

echo "▶ Playwright exit code: $TEST_EXIT_CODE"

# Write captured output to desired file
mv "$TMP_OUTPUT" "$OUTPUT_TEXT"
echo "📝 Saved test output to: $OUTPUT_TEXT"

# Find the generated video file
VIDEO_PATH=$(find test-results -type f -name "video.webm" | head -n 1 || true)

if [ -z "$VIDEO_PATH" ]; then
    echo "❌ ERROR: Could not find video.webm in test-results/"
    exit 2
fi

echo "▶ Found video: $VIDEO_PATH"
echo "▶ Moving to: $OUTPUT_VIDEO"

mv "$VIDEO_PATH" "$OUTPUT_VIDEO"

echo "🎥 Saved video as: $OUTPUT_VIDEO"

# Exit using the test’s exit code
exit $TEST_EXIT_CODE
