"""Shrink a demo PDF, then build the before/after comparison sheet."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import engine  # noqa: E402
import preview  # noqa: E402


def main() -> None:
    demo = ROOT / "demo"
    source = demo / "scanned-report.pdf"
    target_text = sys.argv[1] if len(sys.argv) > 1 else "2 MB"
    output = demo / "scanned-report-compressed.pdf"
    if output.exists():
        output.unlink()

    result = engine.shrink_to_target(
        source, engine.parse_size(target_text), destination=output
    )
    print(
        f"{engine.human_size(result.source_bytes)} -> "
        f"{engine.human_size(result.output_bytes)}  ok={result.ok} "
        f"reason={result.reason} preset={result.chosen.describe() if result.chosen else None}"
    )
    print("ladder:", [(a.preset.name, engine.human_size(a.bytes)) for a in result.attempts])
    print("verify:", result.verification)

    if result.output and Path(result.output).exists():
        sheet = preview.comparison_sheet(
            source,
            Path(result.output),
            demo / "preview.jpg",
            original_label=engine.human_size(result.source_bytes),
            compressed_label=engine.human_size(result.output_bytes),
            caption=(
                f"{result.pages} pages · {result.chosen.describe() if result.chosen else ''}"
            ),
        )
        print("preview:", sheet)


if __name__ == "__main__":
    main()
