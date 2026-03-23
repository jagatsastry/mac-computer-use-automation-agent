#!/bin/bash
# Batch grounding comparison: 10 diverse queries against the most recent desktop screenshot
# Usage: bash scripts/run_grounding_batch.sh

set -e

SCRIPT=".venv/bin/python scripts/compare_grounding.py"
OUT="./grounding_comparison"
FLAGS="--most-recent-screenshot-on-desktop --output-dir $OUT"

queries=(
  "View order details"
  "Search Orders button"
  "Buy it again"
  "the Listerine product image for the March 5 order"
  "Search Amazon text field"
  "Cart icon in the top right"
  "Your Account & Lists"
  "Ordered on February 6, 2026 date text"
  "View your item link"
  "the pet supplies tab in the navigation bar"
)

echo "Running ${#queries[@]} grounding queries..."
echo "Output: $OUT"
echo ""

for q in "${queries[@]}"; do
  echo "================================================================"
  echo "QUERY: $q"
  echo "================================================================"
  $SCRIPT $FLAGS "$q"
  echo ""
  echo ""
done

echo "All done. Results in $OUT"
ls -lt "$OUT"/*.json | head -20
