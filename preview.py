"""Visual proof: render the same page before and after, side by side.

Uses pypdfium2 (PDFium) locally. If it is unavailable the caller degrades to a
text-only report — this module is a nicety, never a requirement.
"""

from __future__ import annotations

import io
from pathlib import Path

__all__ = ["render_page", "comparison_sheet", "available"]

_PAGE_DPI = 96
_PANEL_WIDTH = 720
_GAP = 28
_PAD = 28
_HEADER = 96
_FOOTER = 68
_INK = (24, 27, 35)
_MUTED = (108, 114, 128)
_ACCENT = (34, 82, 190)
_PAPER = (246, 247, 250)

_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Helvetica.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
)


def available() -> bool:
    try:
        import pypdfium2  # noqa: F401
    except Exception:  # noqa: BLE001
        return False
    return True


def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    candidates = _FONT_CANDIDATES
    if bold:
        candidates = (
            "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        ) + candidates
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10
        return ImageFont.load_default()


def render_page(path: Path, page_index: int = 0, dpi: int = _PAGE_DPI):
    """Render one page to a PIL image, or None if it cannot be rendered."""
    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(str(path))
    try:
        if len(document) <= page_index:
            page_index = 0
        page = document[page_index]
        try:
            bitmap = page.render(scale=dpi / 72.0)
            return bitmap.to_pil().convert("RGB")
        finally:
            page.close()
    finally:
        document.close()


def _line_runs(crop):
    """Row ranges that contain ink, grouped into text lines."""
    from PIL import Image

    rows = crop.convert("L").resize((1, crop.height), Image.BOX)
    brightness = list(rows.getdata())
    if not brightness:
        return []
    paper = max(brightness)
    ink = [value < paper - 22 for value in brightness]

    runs = []
    index = 0
    height = len(ink)
    while index < height:
        if ink[index]:
            start = index
            gap = 0
            while index < height and gap < 3:  # small gaps stay inside one line
                if ink[index]:
                    gap = 0
                else:
                    gap += 1
                index += 1
            runs.append((start, max(start + 1, index - gap)))
        else:
            index += 1
    return runs


def _detail_box(page_image, top_fraction: float = 0.30,
                height_fraction: float = 0.11, width_fraction: float = 0.52):
    """Bounding box for the zoom strip, snapped so it holds whole text lines."""
    width, height = page_image.size
    box = (0, int(height * top_fraction),
           int(width * width_fraction), int(height * (top_fraction + height_fraction)))
    try:
        band = page_image.crop(box)
        runs = _line_runs(band)
        # Only lines that sit wholly inside the band may survive, so a sliver at
        # either edge is dropped instead of half-shown.
        kept = [run for run in runs if run[0] >= 2 and run[1] <= band.height - 2]
        if len(kept) >= 2:
            first_start = kept[0][0]
            last_end = kept[-1][1]
            next_ink = next((run[0] for run in runs if run[0] > last_end), None)
            top = max(0, first_start - 8)
            bottom = (
                min(band.height, (last_end + next_ink) // 2)
                if next_ink is not None
                else min(band.height, last_end + 8)
            )
            if bottom - top > 40:
                return (box[0], box[1] + top, box[2], box[1] + bottom)
    except Exception:  # noqa: BLE001 - cosmetic only
        pass
    return box


def _detail_strip(page_image, width: int, box=None):
    """A 2.4x zoom of a text-dense band, so quality loss is visible."""
    from PIL import Image

    crop = page_image.crop(box or _detail_box(page_image))
    ratio = width / crop.width
    return crop.resize((width, int(crop.height * ratio)), Image.LANCZOS)


def comparison_sheet(
    original: Path,
    compressed: Path,
    out_path: Path,
    *,
    original_label: str,
    compressed_label: str,
    caption: str,
    page_index: int = 0,
) -> Path | None:
    """Write a before/after comparison image. Returns the path, or None."""
    from PIL import Image, ImageDraw

    try:
        before = render_page(original, page_index)
        after = render_page(compressed, page_index)
    except Exception:  # noqa: BLE001
        return None
    if before is None or after is None:
        return None

    panels = []
    for image in (before, after):
        ratio = _PANEL_WIDTH / image.width
        panels.append(image.resize((_PANEL_WIDTH, int(image.height * ratio)), Image.LANCZOS))
    strips = []
    detail_box = _detail_box(before)
    for image in (before, after):
        strips.append(_detail_strip(image, _PANEL_WIDTH, detail_box))

    columns = [
        (panels[0], strips[0], "BEFORE", original_label),
        (panels[1], strips[1], "AFTER", compressed_label),
    ]

    column_height = max(p.height for p in panels) + max(s.height for s in strips) + 92
    canvas_width = _PAD * 2 + _PANEL_WIDTH * 2 + _GAP
    canvas_height = _HEADER + column_height + _FOOTER
    canvas = Image.new("RGB", (canvas_width, canvas_height), _PAPER)
    draw = ImageDraw.Draw(canvas)

    draw.text((_PAD, 26), "PDF shrink — before / after", font=_font(34, bold=True), fill=_INK)
    draw.text(
        (_PAD, 66),
        caption,
        font=_font(20),
        fill=_MUTED,
    )

    for index, (panel, strip, tag, label) in enumerate(columns):
        x = _PAD + index * (_PANEL_WIDTH + _GAP)
        y = _HEADER
        colour = _MUTED if index == 0 else _ACCENT
        tag_width = draw.textlength(tag, font=_font(20, bold=True))
        draw.text((x, y), tag, font=_font(20, bold=True), fill=colour)
        draw.text((x + tag_width + 14, y), label, font=_font(20), fill=_INK)
        y += 32

        framed = Image.new("RGB", (panel.width + 2, panel.height + 2), (214, 217, 224))
        framed.paste(panel, (1, 1))
        canvas.paste(framed, (x, y))
        y += framed.height + 12

        draw.text((x, y), "detail · 2.4x", font=_font(17), fill=_MUTED)
        y += 24
        strip_frame = Image.new("RGB", (strip.width + 2, strip.height + 2), (214, 217, 224))
        strip_frame.paste(strip, (1, 1))
        canvas.paste(strip_frame, (x, y))

    draw.text(
        (_PAD, canvas_height - _FOOTER + 16),
        "Rendered locally with PDFium · no upload, no API key · the original file was not modified",
        font=_font(17),
        fill=_MUTED,
    )

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    buffer = io.BytesIO()
    canvas.save(buffer, "JPEG", quality=88, optimize=True)
    out_path.write_bytes(buffer.getvalue())

    for image in (before, after, canvas, *panels, *strips):
        try:
            image.close()
        except Exception:  # noqa: BLE001
            pass
    return out_path
