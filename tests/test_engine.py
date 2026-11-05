"""Tests for the PDF Shrink engine and its Hermes tool handlers.

Run:  python -m pytest tests -q
They use synthesised PDFs (no fixtures to download) and finish in a few seconds.
"""

from __future__ import annotations

import importlib.util
import io
import sys
from pathlib import Path

import pikepdf
import pytest
from PIL import Image

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
