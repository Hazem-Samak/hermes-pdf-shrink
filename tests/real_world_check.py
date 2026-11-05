"""Real-world check on a CUPS-generated vector PDF (fonts + text, no images)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import engine  # noqa: E402

SCRATCH = Path("/Users/corvette/.hermes/cache/scratch")


def build(lines: int) -> Path:
    body = "Quarterly Facilities Report\n\n" + "\n".join(
        f"{i:03d}. The inspection team recorded the condition of the primary "
        "enclosure and the access road serving the north compound, with every "
        "measurement taken by a calibrated laser rangefinder."
        for i in range(1, lines)
    )
    source = SCRATCH / f"vector-{lines}.txt"
    source.write_text(body)
    result = subprocess.run(
        ["cupsfilter", "-i", "text/plain", "-o", "media=A4", str(source)],
        capture_output=True,
    )
    destination = SCRATCH / f"vector-{lines}.pdf"
    destination.write_bytes(result.stdout)
    return destination


def main() -> None:
    for lines in (60, 4000):
        path = build(lines)
        report = engine.inspect_pdf(path)
        print(
            f"\n{path.name}: {engine.human_size(report.bytes)} "
            f"pages={report.pages} images={len(report.images)} "
            f"fonts={report.fonts} share={report.image_share:.2f} "
            f"ver={report.pdf_version}"
        )
        target = max(50_000, report.bytes // 3)
        result = engine.shrink_to_target(path, target)
        print(
            f"  target {engine.human_size(target)} -> ok={result.ok} "
            f"reason={result.reason} out={engine.human_size(result.output_bytes)} "
            f"preset={result.chosen.preset.name if result.chosen else None} "
            f"verified={result.verification.get('ok')}"
        )
        assert engine.sha256_file(path)


if __name__ == "__main__":
    main()
