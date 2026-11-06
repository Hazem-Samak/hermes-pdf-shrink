"""Shrink a demo PDF, then build the before/after comparison sheet.

The output name is derived from the target, so a rehearsal at 500 KB can never
leave a stale file that a later 2 MB run or ``scripts/make_assets.py`` mistakes
for the real result. A JSON sidecar records the exact bytes and preset so the
catalog assets can refuse stale inputs instead of captioning them as 88 DPI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import engine  # noqa: E402
import preview  # noqa: E402


def _slug(num_bytes: int) -> str:
    if num_bytes % 1_000_000 == 0:
        return f"{num_bytes // 1_000_000}mb"
    if num_bytes % 1_000 == 0:
        return f"{num_bytes // 1_000}kb"
    return f"{num_bytes}b"


def _remove(*paths: Path) -> None:
    for path in paths:
        try:
            path.unlink()
        except FileNotFoundError:
            pass


def main() -> None:
    demo = ROOT / "demo"
    source = demo / "scanned-report.pdf"
    target_text = sys.argv[1] if len(sys.argv) > 1 else "2 MB"
    target_bytes = engine.parse_size(target_text)
    if target_bytes == 2_000_000:
        output = demo / "scanned-report-compressed.pdf"
    else:
        output = demo / f"scanned-report-{_slug(target_bytes)}.pdf"
    metadata = output.with_suffix(".json")
    floor_output = demo / f"scanned-report-floor-{_slug(target_bytes)}.pdf"

    _remove(output, metadata, floor_output, floor_output.with_suffix(".json"),
            demo / "preview.jpg")

    result = engine.shrink_to_target(source, target_bytes, destination=output)
    print(
        f"{engine.human_size(result.source_bytes)} -> "
        f"{engine.human_size(result.output_bytes)}  ok={result.ok} "
        f"reason={result.reason} "
        f"preset={result.chosen.describe() if result.chosen else None}"
    )
    print("ladder:", [(a.preset.name, engine.human_size(a.bytes)) for a in result.attempts])
    print("verify:", result.verification)

    if not (
        result.output
        and result.output != result.source
        and Path(result.output).exists()
        and result.verified
    ):
        print("no verified copy was written; assets will not be built from this run")
        return

    output = Path(result.output)
    if not result.ok:
        # A "floor" rehearsal is not a copy that fits the requested target; the
        # filename must say so, not pretend 500 KB was reached.
        _remove(floor_output)
        output.replace(floor_output)
        result.output = floor_output
        output = floor_output
    metadata = output.with_suffix(".json")
    metadata.write_text(
        json.dumps(
            {
                "status": "fits" if result.ok else result.reason,
                "source": str(source),
                "source_sha256": engine.sha256_file(source),
                "source_bytes": result.source_bytes,
                "target_text": target_text,
                "target_bytes": target_bytes,
                "output": str(output),
                "output_sha256": engine.sha256_file(output),
                "output_bytes": result.output_bytes,
                "pages": result.pages,
                "preset": result.chosen.preset.name if result.chosen else None,
                "quality": result.chosen.describe() if result.chosen else None,
                "images_downsampled": (
                    result.chosen.images_downsampled if result.chosen else 0
                ),
                "images_skipped": result.chosen.images_skipped if result.chosen else 0,
                "seconds": result.seconds,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    sheet = preview.comparison_sheet(
        source,
        output,
        demo / "preview.jpg",
        original_label=engine.human_size(result.source_bytes),
        compressed_label=engine.human_size(result.output_bytes),
        caption=(
            f"{result.pages} pages · {result.chosen.describe()}"
            if result.chosen
            else f"{result.pages} pages"
        ),
    )
    print("metadata:", metadata)
    print("preview:", sheet)


if __name__ == "__main__":
    main()
