#!/usr/bin/env python3
"""Build the demo/test PDFs used by the hackathon video.

    python3 scripts/make_demo_pdf.py [output_dir]

Creates, in output_dir (default ``demo/``):

* ``scanned-report.pdf``  24 scanned-looking A4 pages at 300 DPI (~25-30 MB),
  the case this plugin exists for: a scan that is too big to email.
* ``vector-heavy.pdf``    CAD-style vector pages with no images, the honest
  "I cannot safely get this to 2 MB" case.

Both are generated locally so the repo ships no large binaries.
"""

from __future__ import annotations

import io
import random
import sys
from pathlib import Path

import pikepdf
from PIL import Image, ImageDraw, ImageFont

A4 = (595.28, 841.89)
DPI = 300


def _font(size: int) -> ImageFont.ImageFont:
    for candidate in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _paper_texture(width: int, height: int) -> Image.Image:
    """Cheap paper grain: low-res gaussian noise scaled up."""
    grain = Image.effect_noise((width // 12, height // 12), 26).resize(
        (width, height), Image.BILINEAR
    )
    return grain.convert("L")


def _scan_page(page_number: int, total: int) -> Image.Image:
    width = int(A4[0] / 72 * DPI)
    height = int(A4[1] / 72 * DPI)
    page = Image.new("RGB", (width, height), "white")

    grain = _paper_texture(width, height)
    page = Image.blend(page, Image.merge("RGB", (grain, grain, grain)), 0.045)
    grain.close()

    draw = ImageDraw.Draw(page)
    navy = (22, 40, 84)
    body = (30, 32, 36)

    draw.rectangle([0, 0, width, 190], fill=navy)
    draw.text((150, 60), "NORTHWIND FACILITIES", font=_font(58), fill=(255, 255, 255))
    draw.text(
        (150, 128),
        "Annual Site Inspection Report",
        font=_font(40),
        fill=(196, 208, 235),
    )

    y = 280
    draw.text(
        (150, y),
        f"Section {page_number} — Primary enclosure survey",
        font=_font(48),
        fill=navy,
    )
    y += 100

    body_font = _font(33)
    paragraphs = [
        "The inspection team recorded the condition of the primary enclosure, the",
        "secondary containment berm and the access road serving the north compound.",
        "All measurements were taken with a calibrated laser rangefinder and are",
        "reported to the nearest millimetre in the summary table below.",
        "",
        "Surface cracking was observed along the eastern parapet for approximately",
        "eleven metres. No structural displacement was detected; the crack width",
        "remains within the maintenance threshold defined in procedure QP-114.",
        "",
        "Drainage channels were clear at the time of survey. Vegetation growth was",
        "noted at two outfall points and has been scheduled for removal before the",
        "wet season. Lighting circuits 3 and 7 were tested and passed.",
        "",
        "Photographic evidence for each observation is retained in the site archive",
        "under the reference shown at the foot of this page. Any deviation from the",
        "recorded values should be reported to the facilities manager within seven",
        "working days.",
    ]
    for line in paragraphs * 3:
        if y > height - 320:
            break
        draw.text((150, y), line, font=body_font, fill=body)
        y += 52

    # a small stamped-looking table, so the page has fine detail to inspect
    table_y = y + 40
    if table_y < height - 380:
        for row in range(6):
            draw.rectangle(
                [150, table_y + row * 62, width - 150, table_y + row * 62 + 62],
                outline=(120, 120, 128),
                width=2,
            )
            draw.text(
                (172, table_y + row * 62 + 14),
                f"Observation {row + 1:02d}",
                font=_font(28),
                fill=body,
            )
            draw.text(
                (width - 900, table_y + row * 62 + 14),
                f"{random.uniform(0.1, 9.9):.2f} mm",
                font=_font(28),
                fill=(70, 70, 80),
            )

    draw.text(
        (150, height - 150),
        f"Page {page_number} of {total}  ·  ref NWF-2026-{page_number:03d}  ·  scanned",
        font=_font(26),
        fill=(110, 110, 118),
    )
    return page


def build_scanned_report(path: Path, pages: int = 24) -> None:
    pdf = pikepdf.Pdf.new()
    for index in range(1, pages + 1):
        image = _scan_page(index, pages)
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=92, subsampling=0)
        image.close()

        stream = pikepdf.Stream(pdf, buffer.getvalue())
        stream.Type = pikepdf.Name.XObject
        stream.Subtype = pikepdf.Name.Image
        stream.Width = int(A4[0] / 72 * DPI)
        stream.Height = int(A4[1] / 72 * DPI)
        stream.ColorSpace = pikepdf.Name.DeviceRGB
        stream.BitsPerComponent = 8
        stream.Filter = pikepdf.Name.DCTDecode

        page = pdf.add_blank_page(page_size=A4)
        page.Resources = pikepdf.Dictionary(
            XObject=pikepdf.Dictionary(Scan=pdf.make_indirect(stream))
        )
        page.Contents = pdf.make_stream(
            f"q {A4[0]:.2f} 0 0 {A4[1]:.2f} 0 0 cm /Scan Do Q".encode()
        )
    pdf.save(str(path))
    print(f"wrote {path} ({path.stat().st_size / 1e6:.1f} MB, {pages} pages)")


def build_vector_heavy(path: Path, pages: int = 8) -> None:
    """Densely drawn vector pages: nothing to downsample, mostly incompressible."""
    pdf = pikepdf.Pdf.new()
    rng = random.Random(7)
    for _ in range(pages):
        ops = ["0.25 w", "0 0 0 RG"]
        for i in range(9000):
            x1, y1 = rng.uniform(0, 595), rng.uniform(0, 842)
            x2, y2 = x1 + rng.uniform(-70, 70), y1 + rng.uniform(-70, 70)
            ops.append(f"{x1:.3f} {y1:.3f} m {x2:.3f} {y2:.3f} l S")
        page = pdf.add_blank_page(page_size=A4)
        page.Contents = pdf.make_stream("\n".join(ops).encode())
    pdf.save(str(path), compress_streams=True)
    print(f"wrote {path} ({path.stat().st_size / 1e6:.1f} MB, {pages} pages)")


def main(argv: list[str]) -> int:
    outdir = Path(argv[1]) if len(argv) > 1 else Path("demo")
    outdir.mkdir(parents=True, exist_ok=True)
    build_scanned_report(outdir / "scanned-report.pdf")
    build_vector_heavy(outdir / "vector-heavy.pdf")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
