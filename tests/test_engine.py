"""Tests for the PDF Shrink engine and its Hermes tool handlers.

Run:  python -m pytest tests -q
They use synthesised PDFs (no fixtures to download) and finish in a few seconds.
"""

from __future__ import annotations

import importlib.util
import io
import json
import sys
import zlib
from pathlib import Path

import pikepdf
import pytest
from PIL import Image, ImageStat

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import engine  # noqa: E402


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def noisy_image(width: int, height: int) -> Image.Image:
    """A photo-like image: noise compresses poorly, like a real scan."""
    grain = Image.effect_noise((width // 3, height // 3), 70).resize(
        (width, height), Image.BILINEAR
    )
    return Image.merge("RGB", (grain, grain, grain))


def make_pdf(path: Path, pages: int = 3, pixels=(1400, 1900), quality: int = 95,
             page_size=(595.0, 842.0), draw_width: float | None = None) -> Path:
    """Write an image-only PDF. ``draw_width`` shrinks the placement on the page."""
    pdf = pikepdf.Pdf.new()
    width_pt = draw_width if draw_width is not None else page_size[0]
    for _ in range(pages):
        image = noisy_image(*pixels)
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=quality)
        image.close()

        stream = pikepdf.Stream(pdf, buffer.getvalue())
        stream.Type = pikepdf.Name.XObject
        stream.Subtype = pikepdf.Name.Image
        stream.Width = pixels[0]
        stream.Height = pixels[1]
        stream.ColorSpace = pikepdf.Name.DeviceRGB
        stream.BitsPerComponent = 8
        stream.Filter = pikepdf.Name.DCTDecode

        page = pdf.add_blank_page(page_size=page_size)
        page.Resources = pikepdf.Dictionary(
            XObject=pikepdf.Dictionary(Scan=pdf.make_indirect(stream))
        )
        page.Contents = pdf.make_stream(
            f"q {width_pt:.2f} 0 0 {page_size[1]:.2f} 0 0 cm /Scan Do Q".encode()
        )
    pdf.save(str(path))
    return path


def _attach_image(pdf, stream, page_size=(200.0, 200.0), draw=None):
    page = pdf.add_blank_page(page_size=page_size)
    page.Resources = pikepdf.Dictionary(
        XObject=pikepdf.Dictionary(Im0=pdf.make_indirect(stream))
    )
    page.Contents = pdf.make_stream(
        draw
        or f"q {page_size[0]:.2f} 0 0 {page_size[1]:.2f} 0 0 cm /Im0 Do Q".encode()
    )


def _jpeg_stream(pdf, image, quality=92):
    size = image.size
    buffer = io.BytesIO()
    image.save(buffer, "JPEG", quality=quality)
    image.close()
    stream = pikepdf.Stream(pdf, buffer.getvalue())
    stream.Type = pikepdf.Name.XObject
    stream.Subtype = pikepdf.Name.Image
    stream.Width, stream.Height = size
    stream.ColorSpace = pikepdf.Name.DeviceRGB
    stream.BitsPerComponent = 8
    stream.Filter = pikepdf.Name.DCTDecode
    return stream


def make_form_pdf(path: Path) -> Path:
    """An image drawn only from inside a Form XObject."""
    pdf = pikepdf.Pdf.new()
    scan = _jpeg_stream(pdf, noisy_image(1800, 2400))
    scan = pdf.make_indirect(scan)
    form = pikepdf.Stream(pdf, b"q 595 0 0 842 0 0 cm /Im0 Do Q")
    form.Type = pikepdf.Name.XObject
    form.Subtype = pikepdf.Name.Form
    form.BBox = [0, 0, 595, 842]
    form.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=scan))
    form = pdf.make_indirect(form)
    page = pdf.add_blank_page(page_size=(595.28, 841.89))
    page.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Fm0=form))
    page.Contents = pdf.make_stream(b"q 1 0 0 1 0 0 cm /Fm0 Do Q")
    pdf.save(str(path))
    pdf.close()
    return path


def make_smask_pdf(path: Path) -> Path:
    """An RGB image with an 8-bit soft mask of matching dimensions."""
    pdf = pikepdf.Pdf.new()
    base = _jpeg_stream(pdf, noisy_image(800, 800))
    mask = Image.linear_gradient("L").resize((800, 800))
    soft = pikepdf.Stream(pdf, zlib.compress(mask.tobytes()))
    soft.Type = pikepdf.Name.XObject
    soft.Subtype = pikepdf.Name.Image
    soft.Width = soft.Height = 800
    soft.ColorSpace = pikepdf.Name.DeviceGray
    soft.BitsPerComponent = 8
    soft.Filter = pikepdf.Name.FlateDecode
    base.SMask = pdf.make_indirect(soft)
    _attach_image(pdf, base)
    pdf.save(str(path))
    pdf.close()
    return path


def make_image_mask_pdf(path: Path) -> Path:
    """A 1-bit stencil mask with no ColourSpace or BitsPerComponent."""
    pdf = pikepdf.Pdf.new()
    bits = (
        Image.effect_noise((800, 800), 128)
        .point(lambda value: 255 if value > 128 else 0)
        .convert("1")
    )
    stream = pikepdf.Stream(pdf, zlib.compress(bits.tobytes()))
    stream.Type = pikepdf.Name.XObject
    stream.Subtype = pikepdf.Name.Image
    stream.Width = stream.Height = 800
    stream.Filter = pikepdf.Name.FlateDecode
    stream.ImageMask = True
    _attach_image(pdf, stream, draw=b"q 0 0 1 rg 200 0 0 200 0 0 cm /Im0 Do Q")
    pdf.save(str(path))
    pdf.close()
    return path


def make_cmyk_pdf(path: Path) -> Path:
    pdf = pikepdf.Pdf.new()
    image = noisy_image(800, 800).convert("CMYK")
    stream = _jpeg_stream(pdf, image)
    stream.ColorSpace = pikepdf.Name.DeviceCMYK
    _attach_image(pdf, stream)
    pdf.save(str(path))
    pdf.close()
    return path


def make_decode_pdf(path: Path) -> Path:
    """A grayscale image whose /Decode array inverts every sample."""
    pdf = pikepdf.Pdf.new()
    grain = Image.effect_noise((600, 600), 30)
    raw = bytes(min(255, 160 + value // 4) for value in grain.tobytes())
    stream = pikepdf.Stream(pdf, zlib.compress(raw))
    stream.Type = pikepdf.Name.XObject
    stream.Subtype = pikepdf.Name.Image
    stream.Width = stream.Height = 600
    stream.ColorSpace = pikepdf.Name.DeviceGray
    stream.BitsPerComponent = 8
    stream.Filter = pikepdf.Name.FlateDecode
    stream.Decode = [1, 0]
    _attach_image(pdf, stream)
    pdf.save(str(path))
    pdf.close()
    return path


def image_info(path: Path) -> dict | None:
    with pikepdf.Pdf.open(path) as pdf:
        for obj in pdf.objects:
            if isinstance(obj, pikepdf.Stream) and obj.get("/Subtype") == pikepdf.Name.Image:
                image = pikepdf.PdfImage(obj).as_pil_image()
                try:
                    mean = ImageStat.Stat(image).mean[0]
                finally:
                    image.close()
                return {
                    "width": int(obj.Width),
                    "height": int(obj.Height),
                    "filter": str(obj.get("/Filter")),
                    "has_smask": "/SMask" in obj,
                    "has_decode": "/Decode" in obj,
                    "mean": mean,
                }
    return None


@pytest.fixture()
def plugin():
    """Import the plugin package (its directory name has a hyphen)."""
    name = "pdf_shrink_plugin"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name,
        ROOT / "__init__.py",
        submodule_search_locations=[str(ROOT)],
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------- #
# Sizes
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "text,expected",
    [
        ("2 MB", 2_000_000),
        ("2mb", 2_000_000),
        ("1.5 MB", 1_500_000),
        ("500 KB", 500_000),
        ("500kb", 500_000),
        ("2 MiB", 2 * 1024**2),
        ("950000", 950_000),
        ("1,200 KB", 1_200_000),
        (2048, 2048),
    ],
)
def test_parse_size_accepts(text, expected):
    assert engine.parse_size(text) == expected


@pytest.mark.parametrize("text", ["", "MB", "two megabytes", "2 XB", 0, -5])
def test_parse_size_rejects(text):
    with pytest.raises(ValueError):
        engine.parse_size(text)


def test_human_size_is_decimal():
    assert engine.human_size(2_000_000) == "2 MB"
    assert engine.human_size(1_690_000) == "1.69 MB"
    assert engine.human_size(950) == "950 B"


# --------------------------------------------------------------------------- #
# Inspection
# --------------------------------------------------------------------------- #


def test_inspect_finds_images_and_share(tmp_path):
    source = make_pdf(tmp_path / "scan.pdf", pages=2)
    report = engine.inspect_pdf(source)
    assert report.pages == 2
    assert len(report.images) == 2
    assert report.image_share > 0.9
    assert 160 <= report.max_dpi <= 180  # 1400 px across 595 pt == 8.26 in


def test_inspect_measures_displayed_resolution(tmp_path):
    """A 1400px image drawn 283pt wide is 356 DPI, not page width."""
    source = make_pdf(tmp_path / "half.pdf", pages=1, draw_width=283.5)
    report = engine.inspect_pdf(source)
    assert 340 <= report.max_dpi <= 370


# --------------------------------------------------------------------------- #
# Shrinking
# --------------------------------------------------------------------------- #


def test_shrink_reaches_target_without_touching_source(tmp_path):
    source = make_pdf(tmp_path / "big.pdf", pages=3)
    before = engine.sha256_file(source)
    target = source.stat().st_size // 2

    result = engine.shrink_to_target(source, target)

    assert result.ok, result.reason
    assert result.output is not None and result.output.exists()
    assert result.output != source
    assert result.output.stat().st_size <= target
    assert engine.sha256_file(source) == before
    assert result.source_untouched
    assert result.verification["ok"]
    assert result.verification["pages_equal"]
    assert result.chosen.preset.dpi is not None
    assert result.chosen.images_downsampled == 3


def test_shrink_skips_work_when_already_small(tmp_path):
    source = make_pdf(tmp_path / "small.pdf", pages=1, pixels=(300, 400))
    result = engine.shrink_to_target(source, 20_000_000)
    assert result.ok and result.reason == "already_small_enough"
    assert result.output == source
    assert result.attempts == []


def test_shrink_reports_honest_floor_when_target_is_impossible(tmp_path):
    # 169 DPI images can shrink, but not to 5 KB: the floor is delivered and the
    # target is not faked.
    source = make_pdf(tmp_path / "scan.pdf", pages=1)
    result = engine.shrink_to_target(source, 5_000)
    assert result.ok is False
    assert result.reason == "target_unreachable"
    assert result.floor is not None
    assert result.output is not None and result.output.exists()
    assert result.output.stat().st_size > 5_000
    assert result.output.stat().st_size < result.source_bytes
    assert result.source_untouched


def test_shrink_reports_no_gain_when_nothing_can_be_removed(tmp_path):
    # Images are already at ~72 DPI: no rung of the ladder would touch them.
    source = make_pdf(tmp_path / "lowdpi.pdf", pages=1, pixels=(595, 842),
                      quality=98, page_size=(595.0, 842.0))
    result = engine.shrink_to_target(source, 5_000)
    assert result.ok is False
    assert result.reason == "target_unreachable"
    assert result.floor is not None
    assert result.output is None  # nothing meaningfully smaller to hand over
    assert result.output_bytes == result.source_bytes


def test_shrink_never_overwrites_the_source(tmp_path):
    source = make_pdf(tmp_path / "same.pdf", pages=1)
    with pytest.raises(ValueError):
        engine.shrink_to_target(source, 50_000, destination=source)


def test_shrink_writes_the_requested_destination(tmp_path):
    source = make_pdf(tmp_path / "src.pdf", pages=1)
    target = tmp_path / "elsewhere" / "out.pdf"
    result = engine.shrink_to_target(
        source, source.stat().st_size // 2, destination=target
    )
    assert result.ok
    assert target.exists() and result.output == target.resolve()


def test_default_destination_never_collides(tmp_path):
    source = make_pdf(tmp_path / "doc.pdf", pages=1)
    (tmp_path / "doc-compressed.pdf").write_bytes(b"placeholder")
    assert engine._default_destination(source).name == "doc-compressed-2.pdf"


# --------------------------------------------------------------------------- #
# Safety regressions
# --------------------------------------------------------------------------- #


def test_existing_destination_is_never_overwritten(tmp_path):
    source = make_pdf(tmp_path / "src.pdf", pages=1)
    target = tmp_path / "existing.pdf"
    target.write_bytes(b"the user's other file")

    with pytest.raises(engine.DestinationExists):
        engine.shrink_to_target(source, 5_000, destination=target)

    assert target.read_bytes() == b"the user's other file"


def test_verification_failure_does_not_deliver(tmp_path, monkeypatch):
    source = make_pdf(tmp_path / "src.pdf", pages=1)
    destination = tmp_path / "out.pdf"
    monkeypatch.setattr(
        engine,
        "_verify",
        lambda *args, **kwargs: {
            "ok": False,
            "opens": True,
            "pages": 1,
            "pages_equal": False,
            "blank_pages": 0,
            "syntax_errors": 0,
        },
    )

    result = engine.shrink_to_target(source, 5_000, destination=destination)

    assert result.ok is False
    assert result.reason == "verification_failed"
    assert result.output is None
    assert not destination.exists()


def test_a_fitting_copy_is_delivered_even_with_a_tiny_gain():
    assert engine._should_deliver(True, 999, 1_000)
    assert not engine._should_deliver(False, 999, 1_000)
    assert engine._should_deliver(False, 980, 1_000)


def test_form_nested_image_is_downsampled(tmp_path):
    source = make_form_pdf(tmp_path / "form.pdf")
    report = engine.inspect_pdf(source)
    assert len(report.images) == 1
    assert report.reclaimable_bytes > 0

    result = engine.shrink_to_target(
        source, source.stat().st_size // 3, destination=tmp_path / "form-out.pdf"
    )

    assert result.ok, result.reason
    assert result.chosen.images_downsampled == 1
    info = image_info(result.output)
    assert info["width"] < 1800
    assert info["filter"] == "/DCTDecode"


def test_soft_masked_image_is_left_alone(tmp_path):
    source = make_smask_pdf(tmp_path / "alpha.pdf")
    assert engine.inspect_pdf(source).reclaimable_bytes == 0

    result = engine.shrink_to_target(
        source, source.stat().st_size // 2, destination=tmp_path / "alpha-out.pdf"
    )

    assert result.chosen.images_downsampled == 0
    assert result.chosen.images_skipped == 1
    assert result.chosen.preset.lossless


def test_stencil_mask_is_left_alone(tmp_path):
    source = make_image_mask_pdf(tmp_path / "mask.pdf")
    assert engine.inspect_pdf(source).reclaimable_bytes == 0

    result = engine.shrink_to_target(
        source, 1_000, destination=tmp_path / "mask-out.pdf"
    )

    assert result.chosen.images_downsampled == 0
    assert result.chosen.images_skipped == 1


def test_cmyk_image_is_left_alone(tmp_path):
    source = make_cmyk_pdf(tmp_path / "cmyk.pdf")
    assert engine.inspect_pdf(source).reclaimable_bytes == 0

    result = engine.shrink_to_target(
        source, source.stat().st_size // 2, destination=tmp_path / "cmyk-out.pdf"
    )

    assert result.chosen.images_downsampled == 0
    assert result.chosen.images_skipped == 1


def test_decode_array_image_is_left_alone(tmp_path):
    source = make_decode_pdf(tmp_path / "decode.pdf")
    assert engine.inspect_pdf(source).reclaimable_bytes == 0

    result = engine.shrink_to_target(
        source,
        int(source.stat().st_size * 0.3),
        destination=tmp_path / "decode-out.pdf",
    )

    assert result.chosen.images_downsampled == 0
    assert result.chosen.images_skipped == 1
    if result.output is not None:
        assert image_info(result.output)["has_decode"] is True


def test_suggested_target_is_reachable(plugin):
    tools = plugin.tools
    assert tools._suggest_target(1_132_128, 1_133_080) is None

    text = tools._suggest_target(1_693_542, 29_131_000)
    assert text is not None
    assert 1_693_542 <= engine.parse_size(text) < 29_131_000


def make_vector_pdf(path: Path, pages: int = 2, lines: int = 2500) -> Path:
    import random

    pdf = pikepdf.Pdf.new()
    rng = random.Random(11)
    for _ in range(pages):
        ops = ["0.5 w"]
        for _ in range(lines):
            x1, y1 = rng.uniform(0, 595), rng.uniform(0, 842)
            x2, y2 = x1 + rng.uniform(-70, 70), y1 + rng.uniform(-70, 70)
            ops.append(f"{x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")
        page = pdf.add_blank_page(page_size=(595.28, 841.89))
        page.Contents = pdf.make_stream("\n".join(ops).encode())
    pdf.save(str(path), compress_streams=False)
    pdf.close()
    return path


def test_lossless_floor_does_not_suggest_aggressive(tmp_path, plugin):
    source = make_vector_pdf(tmp_path / "vector.pdf")
    payload = json.loads(
        plugin.tools.pdf_shrink(
            {
                "path": str(source),
                "target_size": "1 KB",
                "output_path": str(tmp_path / "vector-out.pdf"),
                "show_preview": False,
            }
        )
    )

    assert payload["status"] == "target_unreachable"
    assert payload["smallest_safe"]["quality"] == "lossless cleanup only"
    assert not any("aggressive" in text for text in payload["suggestions"])
    assert "without changing the document" in payload["message"]


def test_handler_refuses_an_existing_output(tmp_path, plugin):
    source = make_pdf(tmp_path / "src.pdf", pages=1)
    target = tmp_path / "existing.pdf"
    target.write_bytes(b"keep me")

    payload = json.loads(
        plugin.tools.pdf_shrink(
            {
                "path": str(source),
                "target_size": "500 KB",
                "output_path": str(target),
                "show_preview": False,
            }
        )
    )

    assert payload["status"] == "destination_exists"
    assert "never overwrites" in payload["message"]
    assert target.read_bytes() == b"keep me"


def test_handler_reports_verification_failure(tmp_path, monkeypatch, plugin):
    source = make_pdf(tmp_path / "src.pdf", pages=1)
    engine_module = plugin.tools._engine()
    monkeypatch.setattr(
        engine_module,
        "_verify",
        lambda *args, **kwargs: {
            "ok": False,
            "opens": True,
            "pages": 1,
            "pages_equal": False,
            "blank_pages": 0,
            "syntax_errors": 0,
        },
    )

    payload = json.loads(
        plugin.tools.pdf_shrink(
            {
                "path": str(source),
                "target_size": "5 KB",
                "output_path": str(tmp_path / "out.pdf"),
                "show_preview": False,
            }
        )
    )

    assert payload["status"] == "verification_failed"
    assert payload["output"] is None
    assert "failed the re-open check" in payload["message"]


def test_handler_never_claims_the_source_was_untouched(tmp_path, monkeypatch, plugin):
    source = make_pdf(tmp_path / "src.pdf", pages=1)
    engine_module = plugin.tools._engine()
    original = engine_module._apply_preset

    def tamper(pdf, plan, preset):
        result = original(pdf, plan, preset)
        source.write_bytes(source.read_bytes() + b"\n% changed while working")
        return result

    monkeypatch.setattr(engine_module, "_apply_preset", tamper)

    payload = json.loads(
        plugin.tools.pdf_shrink(
            {
                "path": str(source),
                "target_size": "5 KB",
                "output_path": str(tmp_path / "out.pdf"),
                "show_preview": False,
            }
        )
    )

    assert payload["status"] == "source_changed"
    assert payload["original"]["untouched"] is False
    assert "Original untouched" not in payload["message"]
    assert "cannot claim" in payload["message"]


def test_dropped_attachments_are_reported(tmp_path, plugin):
    base = make_pdf(tmp_path / "attached-base.pdf", pages=1)
    source = tmp_path / "attached.pdf"
    with pikepdf.Pdf.open(base) as pdf:
        pdf.attachments["notes.bin"] = b"x" * 300_000
        pdf.save(str(source))

    payload = json.loads(
        plugin.tools.pdf_shrink(
            {
                "path": str(source),
                "target_size": "500 KB",
                "output_path": str(tmp_path / "attached-out.pdf"),
                "drop_attachments": True,
                "show_preview": False,
            }
        )
    )

    assert payload["status"] == "fits"
    assert payload["dropped_attachments"]["count"] == 1
    assert payload["verified"]["attachments"] == 0
    assert "Dropped 1 embedded attachment" in payload["message"]


# --------------------------------------------------------------------------- #
# Tool handlers
# --------------------------------------------------------------------------- #


def test_inspect_handler_returns_json(tmp_path, plugin):
    import json

    source = make_pdf(tmp_path / "scan.pdf", pages=1)
    payload = json.loads(plugin.tools.pdf_inspect({"path": str(source)}))
    assert payload["ok"] is True
    assert payload["status"] == "inspected"
    assert payload["images"]["count"] == 1
    assert payload["verdict"] == "images_dominate"
    assert payload["message"]


def test_shrink_handler_reports_success(tmp_path, plugin):
    import json

    source = make_pdf(tmp_path / "scan.pdf", pages=2)
    payload = json.loads(
        plugin.tools.pdf_shrink(
            {
                "path": str(source),
                "target_size": str(source.stat().st_size // 2),
                "show_preview": False,
            }
        )
    )
    assert payload["ok"] is True
    assert payload["status"] == "fits"
    assert Path(payload["output"]["path"]).exists()
    assert payload["original"]["untouched"] is True
    assert payload["verified"]["page_count_matches"] is True
    assert "→" in payload["message"]


def test_shrink_handler_reports_the_floor(tmp_path, plugin):
    import json

    source = make_pdf(tmp_path / "scan.pdf", pages=1)
    payload = json.loads(
        plugin.tools.pdf_shrink(
            {"path": str(source), "target_size": "5 KB", "show_preview": False}
        )
    )
    assert payload["ok"] is False
    assert payload["status"] == "target_unreachable"
    assert payload["message"].startswith("I could not get this under")
    assert payload["suggestions"]
    assert payload["smallest_safe"]["size"]


def test_handlers_reject_bad_input(tmp_path, plugin):
    import json

    missing = json.loads(plugin.tools.pdf_shrink({"path": str(tmp_path / "nope.pdf"),
                                                  "target_size": "2 MB"}))
    assert missing["ok"] is False and missing["status"] == "not_a_pdf"

    not_pdf = tmp_path / "notes.pdf"
    not_pdf.write_text("hello")
    payload = json.loads(plugin.tools.pdf_inspect({"path": str(not_pdf)}))
    assert payload["status"] == "not_a_pdf"

    source = make_pdf(tmp_path / "ok.pdf", pages=1)
    bad_size = json.loads(plugin.tools.pdf_shrink({"path": str(source),
                                                   "target_size": "two megs"}))
    assert bad_size["status"] == "bad_size"

    same = json.loads(
        plugin.tools.pdf_shrink(
            {"path": str(source), "target_size": "2 MB", "output_path": str(source)}
        )
    )
    assert same["status"] == "bad_request"


def test_registration_declares_both_tools(plugin):
    class Ctx:
        def __init__(self):
            self.tools = []
            self.skills = []

        def register_tool(self, name=None, **kwargs):
            self.tools.append(name)

        def register_skill(self, name, path):
            self.skills.append(name)

    ctx = Ctx()
    plugin.register(ctx)
    assert ctx.tools == ["pdf_shrink", "pdf_inspect"]
    assert ctx.skills == ["pdf-shrink"]
