"""Manual end-to-end check: inspect + shrink the generated demo PDFs.

Run with the dev venv:  python tests/manual_check.py [target]
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import engine  # noqa: E402

DEMO = ROOT / "demo"


def show_inspection(name: str) -> engine.Inspection:
    path = DEMO / name
    if not path.exists():
        raise SystemExit(f"missing {path} - run scripts/make_demo_pdf.py first")
    inspection = engine.inspect_pdf(path)
    print(f"\n== {name} ==")
    print(
        f"  size      {engine.human_size(inspection.bytes)} "
        f"({inspection.bytes} bytes)  pages {inspection.pages}  pdf {inspection.pdf_version}"
    )
    print(
        f"  images    {len(inspection.images)} objects, "
        f"{engine.human_size(inspection.image_bytes)} "
        f"({inspection.image_share * 100:.0f}% of file), max {inspection.max_dpi} DPI"
    )
    print(
        f"  fonts     {inspection.fonts}   attachments {inspection.attachments} "
        f"({engine.human_size(inspection.attachment_bytes)})   dupes {inspection.duplicates}"
    )
    for record in inspection.biggest(3):
        print(
            f"    biggest {record.width}x{record.height} {record.filter_names} "
            f"{engine.human_size(record.raw_bytes)} "
            f"at {round(record.effective_dpi or 0)} DPI on {len(record.pages)} page(s)"
        )
    print(f"  inspect took {inspection.elapsed}s")
    return inspection


def shrink(name: str, target: str) -> engine.ShrinkResult:
    path = DEMO / name
    out = DEMO / f"{path.stem}-target{target.replace(' ', '')}.pdf"
    if out.exists():
        out.unlink()
    started = time.time()
    result = engine.shrink_to_target(path, engine.parse_size(target), destination=out)
    print(f"\n== shrink {name} -> {target} ==")
    print(
        f"  ok={result.ok} reason={result.reason} wall={time.time() - started:.1f}s "
        f"engine={result.seconds}s"
    )
    print(
        f"  {engine.human_size(result.source_bytes)} -> "
        f"{engine.human_size(result.output_bytes)} "
        f"({result.saved_percent:.1f}% smaller)"
    )
    if result.chosen:
        print(f"  preset    {result.chosen.describe()}")
        print(
            f"  images    {result.chosen.images_downsampled} downsampled, "
            f"{result.chosen.images_skipped} skipped, "
            f"{engine.human_size(result.chosen.image_bytes_saved)} recovered"
        )
    print(f"  ladder    {[(a.preset.name, engine.human_size(a.bytes)) for a in result.attempts]}")
    print(f"  verify    {result.verification}")
    print(f"  source untouched: {result.source_untouched}  output: {result.output}")
    return result


if __name__ == "__main__":
    target = sys.argv[1] if len(sys.argv) > 1 else "2 MB"
    show_inspection("scanned-report.pdf")
    shrink("scanned-report.pdf", target)
    show_inspection("vector-heavy.pdf")
    shrink("vector-heavy.pdf", target)
