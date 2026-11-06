"""Target-size PDF compression engine.

Design notes, kept here so the next reader — human or model — knows why:

* The source file is never modified. Every attempt opens the original
  read-only and writes a brand-new file.
* Quality is chosen by measurement, not guesswork: each rung of the ladder is
  rebuilt from the pristine source, so a stronger setting never inherits a
  weaker one's damage.
* Images are downsampled to the resolution they are actually *displayed* at,
  recovered by replaying each page's content stream (``q``/``Q``/``cm``/``Do``)
  to get the graphics matrix. A logo drawn one inch wide is not forced to full
  page resolution.
* When the target is out of reach the engine returns the smallest safe copy
  plus the numbers that prove it, rather than quietly destroying the document.
* Images whose masks or colour model cannot be carried through a re-encode
  (soft/stencil masks, CMYK, non-8-bit samples) are left byte-for-byte alone.
  A copy is verified before it is delivered; a file that fails verification is
  reported as failed, never handed over as a success.
* An existing destination is never overwritten, and the source is hashed before
  and after every run so the "untouched" claim is measured, not assumed.

Pure library on purpose: no Hermes imports, no network, no subprocesses.
"""

from __future__ import annotations

import hashlib
import io
import math
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf
from pikepdf import Name, Pdf

__all__ = [
    "Preset",
    "PRESETS",
    "ImageRecord",
    "Inspection",
    "ShrinkResult",
    "DestinationExists",
    "SourceOverwrite",
    "human_size",
    "parse_size",
    "inspect_pdf",
    "shrink_to_target",
    "sha256_file",
]


# --------------------------------------------------------------------------- #
# Sizes
# --------------------------------------------------------------------------- #

class DestinationExists(ValueError):
    """The requested output path already exists; this tool never overwrites it."""


class SourceOverwrite(ValueError):
    """The requested output path is the source file itself."""


_UNIT_FACTORS = {
    "b": 1,
    "byte": 1,
    "bytes": 1,
    "k": 1000,
    "kb": 1000,
    "kib": 1024,
    "m": 1000**2,
    "mb": 1000**2,
    "mib": 1024**2,
    "g": 1000**3,
    "gb": 1000**3,
    "gib": 1024**3,
}


def parse_size(value) -> int:
    """Parse ``"2 MB"``, ``"1.5mb"``, ``"500 KB"`` or ``2_000_000`` into bytes.

    Decimal units are the default (2 MB == 2,000,000 bytes) because upload
    limits are quoted that way; ``MiB``/``KiB`` are the binary forms.
    """
    if isinstance(value, bool):
        raise ValueError("size must be a number or a string like '2 MB'")
    if isinstance(value, (int, float)):
        if value <= 0:
            raise ValueError("size must be greater than zero")
        return int(value)

    text = str(value).strip().lower().replace(",", "").replace("_", "")
    if not text:
        raise ValueError("empty size")

    digits = ""
    index = 0
    while index < len(text) and (text[index].isdigit() or text[index] == "."):
        digits += text[index]
        index += 1
    unit = text[index:].strip().replace(" ", "")
    if not digits:
        raise ValueError(f"no number found in size {value!r}")

    number = float(digits)
    factor = _UNIT_FACTORS.get(unit or "b")
    if factor is None:
        raise ValueError(
            f"unknown unit {unit!r} in size {value!r} (use B, KB, MB or GB)"
        )
    size = int(round(number * factor))
    if size <= 0:
        raise ValueError("size must be greater than zero")
    return size


def human_size(num_bytes: int) -> str:
    """Format bytes the way upload limits are quoted (decimal units)."""
    if num_bytes < 1000:
        return f"{num_bytes} B"
    for unit, factor in (("GB", 1000**3), ("MB", 1000**2), ("KB", 1000)):
        if num_bytes >= factor:
            value = num_bytes / factor
            if value >= 100:
                return f"{value:.0f} {unit}"
            text = f"{value:.2f}".rstrip("0").rstrip(".")
            return f"{text} {unit}"
    return f"{num_bytes} B"


def sha256_file(path: Path, chunk: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(chunk), b""):
            digest.update(block)
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# Presets — the quality ladder
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Preset:
    name: str
    dpi: int | None  # None => lossless structural pass only
    quality: int | None  # JPEG quality for photographic content

    @property
    def lossless(self) -> bool:
        return self.dpi is None

    def describe(self) -> str:
        if self.lossless:
            return "lossless (no image is re-encoded)"
        return f"{self.dpi} DPI / JPEG quality {self.quality}"


PRESETS: tuple[Preset, ...] = (
    Preset("lossless", None, None),
    Preset("light", 200, 82),
    Preset("balanced", 160, 74),
    Preset("strong", 130, 66),
    Preset("heavy", 110, 58),
    Preset("extreme", 88, 48),
    Preset("minimum", 72, 36),
)

# The ladder used unless the caller opts into "smaller beats readable". Below
# ~88 DPI scanned A4 text stops being dependable, so the default ladder ends
# there and reports an honest floor instead of handing back mush.
SAFE_LADDER: tuple[Preset, ...] = PRESETS[1:-1]
AGGRESSIVE_LADDER: tuple[Preset, ...] = PRESETS[1:]

SAFE_FLOOR_DPI = SAFE_LADDER[-1].dpi


# --------------------------------------------------------------------------- #
# Geometry: where is each image drawn?
# --------------------------------------------------------------------------- #

_IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _mat_mul(m, n):
    """Return ``m x n`` for PDF 6-tuples (apply ``n`` first, then ``m``)."""
    a1, b1, c1, d1, e1, f1 = m
    a2, b2, c2, d2, e2, f2 = n
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


def _numbers(operands, count):
    out = []
    for item in operands[:count]:
        try:
            out.append(float(item))
        except (TypeError, ValueError):
            return None
    return tuple(out) if len(out) == count else None


def _resources_of(page):
    """Page resources, following the Pages-tree inheritance rule."""
    node = page
    for _ in range(32):
        try:
            resources = node.get("/Resources")
        except (AttributeError, KeyError, TypeError):
            resources = None
        if resources is not None:
            return resources
        try:
            node = node.get("/Parent")
        except (AttributeError, KeyError, TypeError):
            return None
        if node is None:
            return None
    return None


def _xobjects(resources):
    if resources is None:
        return {}
    try:
        table = resources.get("/XObject")
    except (AttributeError, KeyError, TypeError):
        return {}
    return table if table is not None else {}


def _page_placements(page) -> dict:
    """Map image XObject objgen -> largest (width_pt, height_pt) it is drawn at.

    Replays the page content stream. Unparseable streams yield an empty map and
    the caller falls back to page-size geometry, which is the right assumption
    for the scans this tool exists to shrink.
    """
    placements: dict = {}
    resources = _resources_of(page)

    try:
        instructions = pikepdf.parse_content_stream(page)
    except Exception:  # noqa: BLE001 - broken content streams are a fact of life
        return placements

    def walk(instructions, resources, ctm, depth, seen_forms):
        stack: list = []
        for instruction in instructions:
            operator = str(instruction.operator)
            operands = instruction.operands
            if operator == "q":
                stack.append(ctm)
            elif operator == "Q":
                if stack:
                    ctm = stack.pop()
            elif operator == "cm":
                values = _numbers(operands, 6)
                if values is not None:
                    ctm = _mat_mul(values, ctm)
            elif operator == "Do" and operands:
                try:
                    name = str(operands[0])
                except Exception:  # noqa: BLE001
                    continue
                entry = _xobjects(resources).get(name)
                if entry is None:
                    continue
                subtype = entry.get("/Subtype")
                key = _obj_key(entry)
                if subtype == Name.Image:
                    width_pt = math.hypot(ctm[0], ctm[1])
                    height_pt = math.hypot(ctm[2], ctm[3])
                    if width_pt <= 0 or height_pt <= 0:
                        continue
                    previous = placements.get(key)
                    if previous is None or (
                        width_pt * height_pt > previous[0] * previous[1]
                    ):
                        placements[key] = (width_pt, height_pt)
                elif subtype == Name.Form and depth < 6 and key not in seen_forms:
                    matrix = _numbers(entry.get("/Matrix") or [], 6) or _IDENTITY
                    form_resources = entry.get("/Resources") or resources
                    try:
                        nested = pikepdf.parse_content_stream(pikepdf.Page(entry))
                    except Exception:  # noqa: BLE001
                        continue
                    walk(
                        nested,
                        form_resources,
                        _mat_mul(matrix, ctm),
                        depth + 1,
                        seen_forms | {key},
                    )

    walk(instructions, resources, _IDENTITY, 0, frozenset())
    return placements


def _obj_key(obj):
    try:
        gen = obj.objgen
    except Exception:  # noqa: BLE001
        return ("mem", id(obj))
    if gen and gen != (0, 0):
        return gen
    return ("mem", id(obj))


# --------------------------------------------------------------------------- #
# Image inventory
# --------------------------------------------------------------------------- #


@dataclass
class ImageRecord:
    key: object
    raw_bytes: int
    width: int
    height: int
    filter_names: tuple
    bits: int
    colorspace: str
    display_pt: tuple | None = None
    pages: list = field(default_factory=list)
    mode: str = ""
    masked: bool = False
    is_mask: bool = False
    has_decode: bool = False

    @property
    def megapixels(self) -> float:
        return self.width * self.height / 1_000_000

    @property
    def effective_dpi(self) -> float | None:
        if not self.display_pt or self.display_pt[0] <= 0:
            return None
        return self.width / (self.display_pt[0] / 72.0)

    @property
    def decodable(self) -> bool:
        """False for encodings that are already efficient and lossy to touch."""
        blocked = {"/JBIG2Decode", "/CCITTFaxDecode"}
        if blocked & set(self.filter_names):
            return False
        return self.bits >= 8

    @property
    def reencode_blocker(self) -> str | None:
        """Why this image must be left alone, or None when it is safe to touch.

        Masks are never resampled independently: a soft mask must keep the same
        pixel dimensions as the image it masks, a stencil mask paints in the
        current fill colour, and a colour-key mask is defined in the image's own
        colour space. Re-encoding any of them without carrying the mask through
        the same transform is how images come back transparent, black or
        inverted. CMYK is also left alone: a naive CMYK→RGB conversion shifts
        print colours, and the plugin has no ICC pipeline. A /Decode array is a
        second colour transform pikepdf versions disagree on applying, so those
        images are left alone rather than risk delivering inverted pixels.
        """
        if self.masked:
            return "has a mask or is an image mask"
        if self.is_mask:
            return "is used as a mask"
        if self.bits != 8:
            return f"{self.bits}-bit samples"
        if self.mode not in ("L", "RGB", "P"):
            return f"{self.mode or 'unknown'} colour"
        if self.has_decode:
            return "has a /Decode array"
        if not self.decodable:
            return "encoding is already efficient"
        return None

    @property
    def reencodable(self) -> bool:
        return self.reencode_blocker is None and self.raw_bytes >= 16_000


def _filter_names(obj) -> tuple:
    try:
        filters = obj.get("/Filter")
    except Exception:  # noqa: BLE001
        return ()
    if filters is None:
        return ()
    if isinstance(filters, list):
        return tuple(str(item) for item in filters)
    return (str(filters),)


def _collect_images(pdf) -> dict:
    """Every distinct image XObject reachable from page resources."""
    entries: dict = {}

    def visit(resources, depth, seen_forms):
        for _name, entry in _xobjects(resources).items():
            try:
                subtype = entry.get("/Subtype")
            except Exception:  # noqa: BLE001
                continue
            key = _obj_key(entry)
            if subtype == Name.Image:
                entries.setdefault(key, entry)
            elif subtype == Name.Form and depth < 6 and key not in seen_forms:
                visit(
                    entry.get("/Resources") or resources,
                    depth + 1,
                    seen_forms | {key},
                )

    for page in pdf.pages:
        visit(_resources_of(page), 0, frozenset())

    # An image that another image uses as a mask is not a picture in its own
    # right; touching it would damage the picture it masks.
    mask_keys = set()
    for entry in entries.values():
        for name in ("/SMask", "/Mask"):
            try:
                candidate = entry.get(name)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(candidate, pikepdf.Stream):
                mask_keys.add(_obj_key(candidate))

    found: dict = {}
    for key, entry in entries.items():
        try:
            width = int(entry.get("/Width") or 0)
            height = int(entry.get("/Height") or 0)
        except Exception:  # noqa: BLE001
            continue
        try:
            raw_bytes = len(entry.read_raw_bytes())
        except Exception:  # noqa: BLE001
            raw_bytes = 0
        try:
            bits = int(entry.get("/BitsPerComponent") or 8)
        except Exception:  # noqa: BLE001
            bits = 8
        try:
            colorspace = str(entry.get("/ColorSpace") or "unknown")
        except Exception:  # noqa: BLE001
            colorspace = "unknown"
        try:
            mode = str(pikepdf.PdfImage(entry).mode)
        except Exception:  # noqa: BLE001
            mode = ""
        try:
            masked = bool(entry.get("/ImageMask")) or any(
                name in entry for name in ("/SMask", "/Mask", "/Matte")
            )
        except Exception:  # noqa: BLE001
            masked = False
        found[key] = ImageRecord(
            key=key,
            raw_bytes=raw_bytes,
            width=width,
            height=height,
            filter_names=_filter_names(entry),
            bits=bits,
            colorspace=colorspace,
            mode=mode,
            masked=masked,
            is_mask=key in mask_keys,
            has_decode="/Decode" in entry,
        )

    for page_index, page in enumerate(pdf.pages):
        resources = _resources_of(page)
        for key, record in found.items():
            if page_index not in record.pages and _page_has_key(resources, key):
                record.pages.append(page_index)
    return found


def _page_has_key(resources, wanted) -> bool:
    """True when ``wanted`` is reachable from this page's resource tree."""
    stack = [(resources, 0)]
    seen = set()
    while stack:
        current, depth = stack.pop()
        if current is None or depth > 6:
            continue
        for _name, entry in _xobjects(current).items():
            try:
                subtype = entry.get("/Subtype")
            except Exception:  # noqa: BLE001
                continue
            key = _obj_key(entry)
            if subtype == Name.Image and key == wanted:
                return True
            if subtype == Name.Form and key not in seen:
                seen.add(key)
                stack.append((entry.get("/Resources") or current, depth + 1))
    return False


# --------------------------------------------------------------------------- #
# Inspection
# --------------------------------------------------------------------------- #


@dataclass
class Inspection:
    path: Path
    bytes: int
    pages: int
    pdf_version: str
    encrypted: bool
    linearized: bool
    has_xmp: bool
    attachments: int
    attachment_bytes: int
    images: dict
    fonts: int
    image_bytes: int
    elapsed: float

    @property
    def image_share(self) -> float:
        return self.image_bytes / self.bytes if self.bytes else 0.0

    @property
    def max_dpi(self) -> int:
        dpis = [r.effective_dpi for r in self.images.values() if r.effective_dpi]
        return int(round(max(dpis))) if dpis else 0

    @property
    def duplicates(self) -> int:
        return sum(1 for r in self.images.values() if len(r.pages) > 1)

    @property
    def reclaimable(self) -> list:
        return [r for r in self.images.values() if r.reencodable]

    @property
    def reclaimable_bytes(self) -> int:
        return sum(r.raw_bytes for r in self.reclaimable)

    def biggest(self, count: int = 3) -> list:
        ordered = sorted(self.images.values(), key=lambda r: r.raw_bytes, reverse=True)
        return [r for r in ordered[:count] if r.raw_bytes > 0]


def _count_fonts(pdf) -> int:
    names = set()
    for page in pdf.pages:
        resources = _resources_of(page)
        if resources is None:
            continue
        try:
            fonts = resources.get("/Font")
        except Exception:  # noqa: BLE001
            continue
        if fonts is None:
            continue
        for _key, value in fonts.items():
            names.add(str(value.get("/BaseFont") or _key))
    return len(names)


def _attachments(pdf) -> tuple:
    try:
        names = pdf.Root.get("/Names")
        embedded = names.get("/EmbeddedFiles") if names is not None else None
        if embedded is None:
            return 0, 0
        listing = embedded.get("/Names") or []
        count = len(listing) // 2
        total = 0
        for index, value in enumerate(listing):
            if index % 2 == 0:
                continue
            try:
                total += len(value.get("/EF").get("/F").read_raw_bytes())
            except Exception:  # noqa: BLE001
                continue
        return count, total
    except Exception:  # noqa: BLE001
        return 0, 0


def inspect_pdf(path: Path) -> Inspection:
    """Read-only survey of a PDF: what makes it heavy, and how shrinkable it is."""
    started = time.monotonic()
    path = Path(path)
    with Pdf.open(path, password="") as pdf:
        placements: dict = {}
        for page in pdf.pages:
            for key, size in _page_placements(page).items():
                previous = placements.get(key)
                if previous is None or size[0] * size[1] > previous[0] * previous[1]:
                    placements[key] = size
        images = _collect_images(pdf)
        for key, record in images.items():
            record.display_pt = placements.get(key)
        attachment_count, attachment_bytes = _attachments(pdf)
        encrypted = pdf.is_encrypted
        linearized = bool(pdf.is_linearized)
        has_xmp = "/Metadata" in pdf.Root
        pages = len(pdf.pages)
        version = str(pdf.pdf_version)
        fonts = _count_fonts(pdf)

    return Inspection(
        path=path,
        bytes=path.stat().st_size,
        pages=pages,
        pdf_version=version,
        encrypted=encrypted,
        linearized=linearized,
        has_xmp=has_xmp,
        attachments=attachment_count,
        attachment_bytes=attachment_bytes,
        images=images,
        fonts=fonts,
        image_bytes=sum(r.raw_bytes for r in images.values()),
        elapsed=round(time.monotonic() - started, 2),
    )


# --------------------------------------------------------------------------- #
# Downsampling
# --------------------------------------------------------------------------- #


def _target_dimensions(record: ImageRecord, preset: Preset, page_box) -> tuple | None:
    """Pixel size for this image at the preset's DPI, or None to leave it alone."""
    if preset.dpi is None or not record.reencodable:
        return None
    display = record.display_pt or page_box
    if not display or display[0] <= 0 or display[1] <= 0:
        return None

    target_w = preset.dpi / 72.0 * display[0]
    target_h = preset.dpi / 72.0 * display[1]
    if record.width <= target_w * 1.02 and record.height <= target_h * 1.02:
        return None  # already at or below the resolution we would give it

    scale = min(target_w / record.width, target_h / record.height)
    new_w = max(1, int(round(record.width * scale)))
    new_h = max(1, int(round(record.height * scale)))
    if new_w * new_h >= 0.92 * record.width * record.height:
        return None  # not enough of a win to be worth a generation of damage
    return new_w, new_h


def _walk_images(resources, depth: int = 0, seen_forms=frozenset()):
    """Yield every image XObject reachable from a resource tree.

    Form XObjects are opaque containers as far as the page content stream is
    concerned, so an image drawn only inside a form is invisible to a walk over
    page resources. Inspection already descends into forms; this keeps the
    rewrite in step with it.
    """
    for _name, entry in _xobjects(resources).items():
        try:
            subtype = entry.get("/Subtype")
        except Exception:  # noqa: BLE001
            continue
        if subtype == Name.Image:
            yield entry
        elif subtype == Name.Form and depth < 6:
            key = _obj_key(entry)
            if key in seen_forms:
                continue
            yield from _walk_images(
                entry.get("/Resources") or resources,
                depth + 1,
                seen_forms | {key},
            )


def _encode_jpeg(pil_image, quality: int):
    """Encode to JPEG and prove the payload decodes at the expected size."""
    from PIL import Image  # local import keeps plugin load cheap

    if pil_image.mode in ("1", "L", "LA"):
        work = pil_image.convert("L")
        colorspace = Name.DeviceGray
    elif pil_image.mode in ("RGB", "RGBA", "P"):
        work = pil_image.convert("RGB")
        colorspace = Name.DeviceRGB
    else:  # CMYK and anything exotic is filtered out before we get here
        raise ValueError(f"unsupported image mode {pil_image.mode}")

    try:
        buffer = io.BytesIO()
        work.save(
            buffer,
            format="JPEG",
            quality=quality,
            optimize=True,
            progressive=False,
            subsampling=2,
        )
        payload = buffer.getvalue()
        probe = Image.open(io.BytesIO(payload))
        try:
            probe.load()
            size = probe.size
        finally:
            probe.close()
        if size != work.size:
            raise ValueError("JPEG round-trip changed the image dimensions")
        return payload, colorspace, size
    finally:
        if work is not pil_image:
            work.close()


def _write_jpeg(stream, payload: bytes, colorspace, size: tuple) -> None:
    """Replace a stream body and remove keys that no longer describe it."""
    stream.write(payload, filter=Name.DCTDecode)
    for key in ("/Decode", "/DecodeParms", "/Alternates"):
        if key in stream:
            del stream[key]
    stream.Width = size[0]
    stream.Height = size[1]
    stream.ColorSpace = colorspace
    stream.BitsPerComponent = 8


def _apply_preset(pdf, plan: dict, preset: Preset) -> tuple:
    """Rewrite every planned image in ``pdf`` at this preset. Returns counters."""
    from PIL import Image  # local import keeps plugin load cheap

    downsampled = 0
    skipped = 0
    saved = 0
    processed: set = set()

    for _page_index, page in enumerate(pdf.pages):
        try:
            box = page.MediaBox
            page_box = (float(box[2]) - float(box[0]), float(box[3]) - float(box[1]))
        except Exception:  # noqa: BLE001
            page_box = (612.0, 792.0)

        for entry in _walk_images(_resources_of(page)):
            key = _obj_key(entry)
            if key not in plan or key in processed:
                continue
            processed.add(key)
            record = plan[key]
            if record.reencode_blocker is not None:
                skipped += 1
                continue
            dims = _target_dimensions(record, preset, page_box)
            if dims is None:
                continue

            before = record.raw_bytes
            pil = None
            resized = None
            try:
                pil = pikepdf.PdfImage(entry).as_pil_image()
                resized = pil.resize(dims, Image.LANCZOS)
                payload, colorspace, size = _encode_jpeg(
                    resized, preset.quality or 75
                )
            except Exception:  # noqa: BLE001 - undecodable image: leave it be
                skipped += 1
                continue
            finally:
                for image in (resized, pil):
                    try:
                        if image is not None:
                            image.close()
                    except Exception:  # noqa: BLE001
                        pass

            if len(payload) >= before:
                # A re-encode that does not save bytes is all cost and no gain.
                skipped += 1
                continue
            try:
                _write_jpeg(entry, payload, colorspace, size)
            except Exception:  # noqa: BLE001
                skipped += 1
                continue
            downsampled += 1
            saved += before - len(payload)

    return downsampled, skipped, saved


def _save(pdf, destination: Path, preset: Preset, drop_attachments: bool) -> None:
    if drop_attachments:
        try:
            names = pdf.Root.get("/Names")
            if names is not None and "/EmbeddedFiles" in names:
                del names["/EmbeddedFiles"]
        except Exception:  # noqa: BLE001
            pass
    if preset.lossless:
        try:
            pdf.remove_unreferenced_resources()
        except Exception:  # noqa: BLE001
            pass
    pdf.save(
        str(destination),
        compress_streams=True,
        stream_decode_level=pikepdf.StreamDecodeLevel.generalized,
        object_stream_mode=pikepdf.ObjectStreamMode.generate,
        recompress_flate=True,
        fix_metadata_version=True,
        deterministic_id=True,
    )


# --------------------------------------------------------------------------- #
# The ladder
# --------------------------------------------------------------------------- #


@dataclass
class Attempt:
    preset: Preset
    path: Path
    bytes: int
    images_downsampled: int
    images_skipped: int
    image_bytes_saved: int
    seconds: float

    def describe(self) -> str:
        if self.preset.lossless:
            return "lossless cleanup only"
        if self.images_downsampled == 0:
            if self.images_skipped:
                return (
                    "structure cleanup only "
                    f"({self.images_skipped} image(s) left alone)"
                )
            return "structure cleanup only (no image needed re-encoding)"
        return self.preset.describe()


@dataclass
class ShrinkResult:
    ok: bool
    reason: str
    source: Path
    source_bytes: int
    source_sha256: str
    target_bytes: int
    output: Path | None
    output_bytes: int
    chosen: Attempt | None
    attempts: list
    floor: Attempt | None
    pages: int
    verified: bool
    verification: dict
    drop_attachments: bool
    seconds: float
    inspection: Inspection | None = None
    source_untouched: bool = True
    aggressive: bool = False

    @property
    def fits(self) -> bool:
        return self.ok and self.output_bytes <= self.target_bytes

    @property
    def saved_bytes(self) -> int:
        return max(0, self.source_bytes - self.output_bytes)

    @property
    def saved_percent(self) -> float:
        if not self.source_bytes:
            return 0.0
        return 100.0 * self.saved_bytes / self.source_bytes


def _verify(output: Path, expected_pages: int) -> dict:
    """Reopen the result and prove it is a whole, readable document."""
    report = {
        "pages_equal": False,
        "opens": False,
        "pages": 0,
        "blank_pages": 0,
        "syntax_errors": 0,
    }
    try:
        with Pdf.open(output) as check:
            report["opens"] = True
            pages = list(check.pages)
            report["pages"] = len(pages)
            report["pages_equal"] = len(pages) == expected_pages
            blank = 0
            for page in pages:
                try:
                    if page.get("/Contents") is None and _xobjects(
                        _resources_of(page)
                    ) == {}:
                        blank += 1
                except Exception:  # noqa: BLE001
                    pass
            report["blank_pages"] = blank
            try:
                report["attachments"] = _attachments(check)[0]
            except Exception:  # noqa: BLE001
                report["attachments"] = None
            checker = getattr(check, "check_pdf_syntax", None)
            if checker is None:
                report["syntax_checked"] = False
            else:
                try:
                    problems = list(checker())
                except Exception as exc:  # noqa: BLE001
                    report["syntax_checked"] = False
                    report["syntax_error"] = f"{type(exc).__name__}: {exc}"
                    problems = []
                else:
                    report["syntax_checked"] = True
                    report["syntax_errors"] = len(problems)
                    if problems:
                        report["syntax_problems"] = problems[:5]
    except Exception as exc:  # noqa: BLE001
        report["error"] = f"{type(exc).__name__}: {exc}"
    report["ok"] = bool(
        report["opens"]
        and report["pages_equal"]
        and report["blank_pages"] == 0
        and report["syntax_errors"] == 0
    )
    return report


def shrink_to_target(
    source: Path,
    target_bytes: int,
    *,
    destination: Path | None = None,
    drop_attachments: bool = False,
    aggressive: bool = False,
    max_seconds: float = 240.0,
    inspection: Inspection | None = None,
) -> ShrinkResult:
    """Shrink ``source`` to ``target_bytes``, never writing to ``source``."""
    started = time.monotonic()
    source = Path(source).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(f"no such file: {source}")
    if source.suffix.lower() != ".pdf":
        raise ValueError(f"not a .pdf file: {source.name}")

    source_bytes = source.stat().st_size
    source_hash = sha256_file(source)
    inspection = inspection or inspect_pdf(source)
    pages = inspection.pages

    workdir = Path(tempfile.mkdtemp(prefix="pdfshrink-", dir=str(source.parent)))
    attempts: list[Attempt] = []

    def run(preset: Preset) -> Attempt:
        started_at = time.monotonic()
        candidate = workdir / f"{preset.name}.pdf"
        with Pdf.open(source, password="") as pdf:
            plan = {}
            for key, record in inspection.images.items():
                plan[key] = record
            downsampled, skipped, saved = _apply_preset(pdf, plan, preset)
            _save(pdf, candidate, preset, drop_attachments)
        attempt = Attempt(
            preset=preset,
            path=candidate,
            bytes=candidate.stat().st_size,
            images_downsampled=downsampled,
            images_skipped=skipped,
            image_bytes_saved=saved,
            seconds=round(time.monotonic() - started_at, 2),
        )
        attempts.append(attempt)
        return attempt

    def finish(result: ShrinkResult) -> ShrinkResult:
        # The "original untouched" claim is a measurement, not a comment: take
        # it on every exit path, including the early ones.
        try:
            result.source_untouched = sha256_file(source) == source_hash
        except OSError:
            result.source_untouched = False
        if not result.source_untouched:
            result.ok = False
            result.reason = "source_changed"
        shutil.rmtree(workdir, ignore_errors=True)
        return result

    try:
        base = ShrinkResult(
            ok=False,
            reason="",
            source=source,
            source_bytes=source_bytes,
            source_sha256=source_hash,
            target_bytes=target_bytes,
            output=None,
            output_bytes=source_bytes,
            chosen=None,
            attempts=attempts,
            floor=None,
            pages=pages,
            verified=False,
            verification={},
            drop_attachments=drop_attachments,
            seconds=0.0,
            inspection=inspection,
            aggressive=aggressive,
        )

        if source_bytes <= target_bytes:
            base.ok = True
            base.reason = "already_small_enough"
            base.output = source
            base.output_bytes = source_bytes
            base.verified = True
            base.verification = {
                "ok": True,
                "opens": True,
                "pages": pages,
                "pages_equal": True,
                "blank_pages": 0,
                "syntax_errors": 0,
                "attachments": inspection.attachments,
            }
            base.seconds = round(time.monotonic() - started, 2)
            return finish(base)

        # Rung 1: lossless structural cleanup. Always worth measuring — it costs
        # nothing in quality and sometimes lands inside the target on its own.
        lossless = run(PRESETS[0])
        shrinkable = sum(
            1 for record in inspection.images.values() if record.reencodable
        )
        if lossless.bytes <= target_bytes:
            chosen = lossless
        elif shrinkable == 0:
            # Nothing in here is an image we could safely re-encode: walking the
            # ladder would rebuild the same file six times and prove nothing.
            chosen = lossless
        else:
            ladder = list(AGGRESSIVE_LADDER if aggressive else SAFE_LADDER)
            start = _estimate_start(source_bytes, lossless.bytes, target_bytes, ladder)
            chosen = None

            probe = run(ladder[start])
            if probe.bytes <= target_bytes:
                best = probe
                # walk back toward better quality while it still fits
                for index in range(start - 1, -1, -1):
                    if time.monotonic() - started > max_seconds:
                        break
                    milder = run(ladder[index])
                    if milder.bytes <= target_bytes:
                        best = milder
                    else:
                        break
                chosen = best
            else:
                best = probe
                for index in range(start + 1, len(ladder)):
                    if time.monotonic() - started > max_seconds:
                        break
                    stronger = run(ladder[index])
                    if stronger.bytes < best.bytes:
                        best = stronger
                    if stronger.bytes <= target_bytes:
                        chosen = stronger
                        break
                if chosen is None:
                    chosen = best

        base.chosen = chosen
        base.attempts = attempts
        fits = chosen is not None and chosen.bytes <= target_bytes

        # Verify the candidate before it is allowed near the user's directory.
        verification = _verify(chosen.path, pages) if chosen is not None else {}
        if chosen is None or not verification.get("ok"):
            base.ok = False
            base.reason = "verification_failed"
            base.floor = chosen
            base.output = None
            base.output_bytes = source_bytes
            base.verified = False
            base.verification = verification
            base.seconds = round(time.monotonic() - started, 2)
            return finish(base)

        # A copy that fits the limit is always delivered, even when the saving
        # is small: refusing it because it is "only" 1% smaller would throw away
        # a file the user asked for. On a miss, deliver the smallest attempt
        # only when it saves at least 2%; a rounding-error copy is not worth a
        # new file, and the report names the best attempt either way.
        deliver = _should_deliver(fits, chosen.bytes, source_bytes)
        if not deliver:
            base.ok = False
            base.reason = "target_unreachable"
            base.floor = chosen
            base.output = None
            # Nothing smaller exists, so the honest number is the original size.
            base.output_bytes = source_bytes
            base.verified = False
            base.verification = verification
            base.seconds = round(time.monotonic() - started, 2)
            return finish(base)

        if destination is None:
            while True:
                destination = _default_destination(source)
                try:
                    reserved = _reserve_destination(destination)
                    break
                except FileExistsError:
                    continue
        else:
            destination = Path(destination).expanduser().resolve()
            if destination == source:
                raise SourceOverwrite(
                    "refusing to overwrite the source file; pick another name"
                )
            if destination.exists():
                try:
                    same = os.path.samefile(destination, source)
                except OSError:
                    same = False
                if same:
                    raise SourceOverwrite(
                        "refusing to overwrite the source file; pick another name"
                    )
                raise DestinationExists(
                    f"{destination} already exists; PDF Shrink never overwrites a file"
                )
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                reserved = _reserve_destination(destination)
            except FileExistsError as exc:
                raise DestinationExists(
                    f"{destination} already exists; PDF Shrink never overwrites a file"
                ) from exc

        candidate_hash = sha256_file(chosen.path)
        _copy_to_destination(chosen.path, destination, reserved)
        chosen.path = destination

        # The copied bytes are the bytes that already passed verification. Hash
        # them instead of parsing the whole document a second time; if the hash
        # differs, fall back to a full re-open.
        if sha256_file(destination) == candidate_hash:
            verification["copy_hash_match"] = True
        else:
            verification = _verify(destination, pages)
            verification["copy_hash_match"] = False
            verification["ok"] = False
            verification["error"] = (
                "the copied bytes differ from the verified candidate"
            )
        base.output = destination
        base.output_bytes = destination.stat().st_size
        base.verified = bool(verification.get("ok"))
        base.verification = verification
        base.floor = None if fits else chosen
        base.ok = bool(fits and base.verified)
        if not verification.get("ok"):
            base.reason = "verification_failed"
        elif fits:
            base.reason = "fits"
        else:
            base.reason = "target_unreachable"
        base.seconds = round(time.monotonic() - started, 2)
        return finish(base)
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise


def _reserve_destination(destination: Path) -> int:
    """Create the destination atomically, failing if anything already exists."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    return os.open(str(destination), flags, 0o666)


def _copy_to_destination(candidate: Path, destination: Path, fd: int) -> None:
    try:
        with os.fdopen(fd, "wb") as out, open(candidate, "rb") as source_file:
            shutil.copyfileobj(source_file, out, 1 << 20)
            out.flush()
            try:
                os.fsync(out.fileno())
            except OSError:  # not every filesystem supports it
                pass
    except BaseException:
        try:
            os.unlink(destination)
        except OSError:
            pass
        raise


def _should_deliver(fits: bool, chosen_bytes: int, source_bytes: int) -> bool:
    """A fitting copy is always worth delivering, however small the saving.

    On a miss, a sub-2% saving is not worth writing another file; the report
    says what the best attempt was instead.
    """
    if fits:
        return True
    return chosen_bytes * 100 <= source_bytes * 98


def _default_destination(source: Path) -> Path:
    stem = source.with_suffix("")
    candidate = Path(f"{stem}-compressed.pdf")
    index = 2
    while candidate.exists():
        candidate = Path(f"{stem}-compressed-{index}.pdf")
        index += 1
    return candidate


def _estimate_start(
    source_bytes: int, lossless_bytes: int, target_bytes: int, ladder: list
) -> int:
    """Pick a first rung instead of always starting at the mildest one."""
    if lossless_bytes <= 0:
        return len(ladder) // 2
    ratio = max(target_bytes, 1) / lossless_bytes
    if ratio >= 1:
        return 0
    # Image bytes scale roughly with the square of the DPI, so this is a decent
    # first guess; the ladder then walks up or down from it.
    estimated_dpi = ladder[0].dpi * math.sqrt(ratio)
    for index, preset in enumerate(ladder):
        if preset.dpi <= estimated_dpi:
            return index
    return len(ladder) - 1
