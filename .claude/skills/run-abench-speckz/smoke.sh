#!/usr/bin/env bash
# smoke.sh — end-to-end driver for abench-speckz
# Usage: bash .claude/skills/run-abench-speckz/smoke.sh [workdir]
# workdir defaults to /tmp/abench-smoke-$$

set -euo pipefail

REPO="$(cd "$(dirname "$0")/../../.." && pwd)"
WORKDIR="${1:-/tmp/abench-smoke-$$}"
OUT="$WORKDIR/out"
RESULTS="$WORKDIR/results"

echo "=== abench-speckz smoke test ==="
echo "repo:    $REPO"
echo "workdir: $WORKDIR"
echo

# ── 1. Install ────────────────────────────────────────────────────────────────
if ! command -v abench-speckz &>/dev/null; then
  echo "[install] pip install -e .[dev]"
  pip install -e "$REPO/[dev]" -q
fi
echo "[ok] $(abench-speckz --help 2>&1 | head -1)"

# ── 2. Gen ────────────────────────────────────────────────────────────────────
echo
echo "[gen] dry-run (smoke profile = 1 combo)"
abench-speckz gen "$REPO/examples/full.yaml" --dry-run
echo
echo "[gen] writing to $OUT"
rm -rf "$OUT"
abench-speckz gen "$REPO/examples/full.yaml" --out "$OUT"
NCOMBOS=$(python3 -c "import json; print(len(json.load(open('$OUT/manifest.json'))))")
echo "[ok] wrote $NCOMBOS combo(s)"

# ── 3. Run ────────────────────────────────────────────────────────────────────
echo
TOOL="$REPO/.claude/skills/run-abench-speckz/fake.tool.yaml"
echo "[run] 3 reps with fake tool"
rm -rf "$RESULTS"
abench-speckz run "$OUT/" \
  --tool "$TOOL" \
  --repeat 3 \
  --results "$RESULTS"

# ── 4. skip-existing check ────────────────────────────────────────────────────
echo
echo "[run] skip-existing (expect 3 skipped)"
SUMMARY=$(abench-speckz run "$OUT/" \
  --tool "$TOOL" \
  --repeat 3 \
  --results "$RESULTS" \
  --skip-existing 2>&1)
echo "$SUMMARY"
echo "$SUMMARY" | grep -q "skipped=3" || { echo "FAIL: expected skipped=3"; exit 1; }

# ── 5. Stats ──────────────────────────────────────────────────────────────────
echo
echo "[stats] table"
abench-speckz stats "$RESULTS/"

echo
echo "[stats] HTML report"
abench-speckz stats "$RESULTS/" --report "$WORKDIR/report.html"
test -f "$WORKDIR/report.html" || { echo "FAIL: report.html not written"; exit 1; }
REPORT_SIZE=$(wc -c < "$WORKDIR/report.html")
echo "[ok] report.html — $REPORT_SIZE bytes"

# ── 6. rebuild-aggregates ─────────────────────────────────────────────────────
echo
echo "[rebuild-aggregates]"
abench-speckz rebuild-aggregates "$RESULTS/"
echo "[ok]"

echo
echo "=== all checks passed ==="
echo "report: $WORKDIR/report.html"
