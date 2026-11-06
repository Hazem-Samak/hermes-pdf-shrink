#!/usr/bin/env python3
"""Build the catalog card image and the screenshot from the demo PDFs.

    python3 scripts/make_assets.py [demo_dir] [out_dir]

Writes ``assets/banner.png`` (1200x600, the 2:1 catalog card) and
``assets/screenshot.jpg`` (the before/after comparison sheet). Both are committed
because the catalog pins image URLs to the entry's commit.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import engine  # noqa: E402
import preview  # noqa: E402

INK = (18, 21, 30)
PAPER = (244, 246, 250)
ACCENT = (52, 104, 232)
MUTED = (128, 136, 152)

FONTS_BOLD = (
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
)
FONTS_REGULAR = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
)


def font(size: int, bold: bool = False):
    for path in FONTS_BOLD if bold else FONTS_REGULAR:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def banner(page_image: Image.Image, out_path: Path, size_in: str, size_out: str) -> Path:
    width, height = 1200, 600
    canvas = Image.new("RGB", (width, height), INK)
    draw = ImageDraw.Draw(canvas)

    draw.text((64, 92), "PDF Shrink", font=font(76, True), fill=(255, 255, 255))
    draw.text(
        (66, 190),
        "Hit the size limit without wrecking the document.",
        font=font(30),
        fill=(178, 188, 208),
    )

    lines = [
        (size_in, "in", MUTED),
        (size_out, "out", (140, 210, 160)),
    ]
    x = 66
    for value, label, colour in lines:
        draw.text((x, 268), value, font=font(54, True), fill=colour)
        draw.text((x, 330), label, font=font(26), fill=(120, 130, 150))
        x += 300

    draw.text(
        (66, 400),
        "on your machine · no upload · no API key",
        font=font(26),
        fill=(150, 160, 180),
    )
    draw.text(
        (66, 440),
        "measured quality ladder · honest floor when 2 MB is impossible",
        font=font(24),
        fill=(110, 120, 140),
    )

    # page render on the right, top-aligned with the wordmark
    thumb_height = 460
    ratio = thumb_height / page_image.height
    thumb = page_image.resize((int(page_image.width * ratio), thumb_height), Image.LANCZOS)
    shadow = Image.new("RGB", (thumb.width + 8, thumb.height + 8), (28, 32, 44))
    canvas.paste(shadow, (width - thumb.width - 92, 92))
    canvas.paste(thumb, (width - thumb.width - 96, 88))
    draw.rectangle(
        [width - thumb.width - 96, 88, width - 96, 88 + thumb_height],
        outline=(60, 68, 88),
        width=2,
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, "PNG", optimize=True)
    return out_path


def main(argv: list[str]) -> int:
    import json

    demo = Path(argv[1]) if len(argv) > 1 else ROOT / "demo"
    assets = Path(argv[2]) if len(argv) > 2 else ROOT / "assets"
    original = demo / "scanned-report.pdf"
    compressed = demo / "scanned-report-compressed.pdf"
    metadata_path = compressed.with_suffix(".json")
    if not original.exists() or not compressed.exists() or not metadata_path.exists():
        raise SystemExit(
            "run scripts/build_demo.sh (or tests/render_preview.py '2 MB' first)"
        )

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if metadata.get("target_bytes") != 2_000_000:
        raise SystemExit(
            "the rehearsal copy was made for a different target; rerun "
            "tests/render_preview.py '2 MB' before building catalog assets"
        )
    if metadata.get("status") != "fits":
        raise SystemExit(
            "the rehearsal did not reach 2 MB; the catalog captions claim it did"
        )
    if metadata.get("output_sha256") != engine.sha256_file(compressed):
        raise SystemExit(
            "scanned-report-compressed.pdf does not match its metadata (stale or "
            "edited); rerun tests/render_preview.py '2 MB'"
        )
    if metadata.get("source_sha256") != engine.sha256_file(original):
        raise SystemExit(
            "scanned-report.pdf changed after the rehearsal; rerun "
            "scripts/build_demo.sh from scratch"
        )

    inspection = engine.inspect_pdf(original)
    out_size = compressed.stat().st_size
    page = preview.render_page(original, 0, dpi=110)
    print(
        banner(
            page,
            assets / "banner.png",
            engine.human_size(inspection.bytes),
            engine.human_size(out_size),
        )
    )
    print(
        preview.comparison_sheet(
            original,
            compressed,
            assets / "screenshot.jpg",
            original_label=engine.human_size(inspection.bytes),
            compressed_label=engine.human_size(out_size),
            caption=(
                f"{inspection.pages} pages · "
                f"{metadata.get('quality') or 'no image re-encoded'}"
            ),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
