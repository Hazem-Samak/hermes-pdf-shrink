"""Measure every rung of the ladder on a file — how small does each preset get?"""

from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pikepdf  # noqa: E402
import engine  # noqa: E402


def main(path: str) -> None:
    source = Path(path)
    inspection = engine.inspect_pdf(source)
    print(
        f"{source.name}: {engine.human_size(inspection.bytes)}  "
        f"pages={inspection.pages} images={len(inspection.images)} "
        f"max_dpi={inspection.max_dpi}"
    )
    with tempfile.TemporaryDirectory() as tmp:
        for preset in engine.PRESETS:
            started = time.time()
            out = Path(tmp) / f"{preset.name}.pdf"
            with pikepdf.Pdf.open(source) as pdf:
                plan = dict(inspection.images)
                down, skipped, saved = engine._apply_preset(pdf, plan, preset)
                engine._save(pdf, out, preset, False)
            print(
                f"  {preset.name:<9} {engine.human_size(out.stat().st_size):>10}  "
                f"({preset.describe():<34}) images {down:>3}/{len(inspection.images)} "
                f"skipped {skipped:>2}  {time.time() - started:5.1f}s"
            )


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "demo/scanned-report.pdf")
