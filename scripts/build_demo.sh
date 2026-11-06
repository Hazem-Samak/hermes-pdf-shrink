#!/bin/sh
# Rebuild the demo PDFs, the 2 MB rehearsal copy, and the catalog assets.
#
# Usage: scripts/build_demo.sh
#
# Every stage is deterministic (fixed seed, fixed target). The rehearsal writes
# demo/scanned-report-compressed.json, and make_assets refuses to build if that
# metadata or either PDF hash no longer matches, so a 500 KB rehearsal can never
# be captioned as the 2 MB result.
set -eu
cd "$(dirname "$0")/.."

python3 scripts/make_demo_pdf.py
python3 tests/render_preview.py "2 MB"
python3 scripts/make_assets.py
