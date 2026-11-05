"""Tool handlers for PDF Shrink.

Every handler returns a JSON string — success and failure alike — and never
raises. Heavy imports (pikepdf, PDFium) happen inside the handlers so plugin
registration stays instant and a missing optional dependency degrades to a clear
message instead of a plugin that will not load.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

__all__ = ["pdf_shrink", "pdf_inspect"]

PAGE_LIMIT_NOTE = "the original was not modified"

_ENGINE = None
_PREVIEW = None


class DependencyMissing(RuntimeError):
    """Raised when the plugin's Python libraries are not in this environment."""


def _engine():
    global _ENGINE
    if _ENGINE is None:
        try:
            from . import engine as engine_module
        except ModuleNotFoundError as exc:
            raise DependencyMissing(str(exc)) from exc
        _ENGINE = engine_module
    return _ENGINE


def _missing_dependency(exc: Exception) -> str:
    module = str(exc).split("'")[1] if "'" in str(exc) else str(exc)
    return _fail(
        "missing_dependency",
        f"PDF Shrink's PDF library ({module}) is not installed in this Hermes "
        "environment, so nothing ran and no file was changed. Fix it with "
        "`hermes plugins install pdf-shrink --force` (or `hermes pm repair`), "
        "then restart Hermes.",
        missing=module,
    )


def _preview():
    global _PREVIEW
    if _PREVIEW is None:
        from . import preview as preview_module

        _PREVIEW = preview_module
    return _PREVIEW


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _data_dir() -> Path:
    """Per-profile plugin data directory (survives updates)."""
    try:
        from plugins.plugin_storage import plugin_data_dir

        return Path(plugin_data_dir("pdf-shrink"))
    except Exception:  # noqa: BLE001 - standalone use / tests
        root = Path(os.environ.get("HERMES_HOME") or (Path.home() / ".hermes"))
        directory = root / "plugin-data" / "pdf-shrink"
        directory.mkdir(parents=True, exist_ok=True)
        return directory


def _resolve(path_text) -> Path:
    path = Path(str(path_text)).expanduser()
    if not path.is_absolute():
        path = Path.cwd() / path
    return path.resolve()


def _fail(status: str, message: str, **extra) -> str:
    payload = {"ok": False, "status": status, "message": message}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False)


def _check_pdf(path: Path) -> str | None:
    """Return an error message when ``path`` is not a readable PDF."""
    if not path.exists():
        return f"There is no file at {path}."
    if path.is_dir():
        return f"{path} is a folder, not a PDF."
    if path.suffix.lower() != ".pdf":
        return f"{path.name} is not a .pdf file."
    try:
        with open(path, "rb") as handle:
            header = handle.read(5)
    except OSError as exc:
        return f"I could not read {path.name}: {exc}"
    if not header.startswith(b"%PDF-"):
        return f"{path.name} does not look like a PDF (no %PDF header)."
    return None


def _short_sha(digest: str) -> str:
    return digest[:12] if digest else ""


def _suggest_target(floor_bytes: int) -> str:
    """A round number just above the honest floor, for the 'raise it to' hint."""
    engine = _engine()
    step = 500_000
    rounded = ((floor_bytes // step) + 1) * step
    if rounded < 1_000_000:
        rounded = 1_000_000
    return engine.human_size(rounded)


def _describe_attempt(engine, attempt) -> dict:
    return {
        "preset": attempt.preset.name,
        "quality": attempt.preset.describe(),
        "size": engine.human_size(attempt.bytes),
        "bytes": attempt.bytes,
        "images_downsampled": attempt.images_downsampled,
        "images_skipped": attempt.images_skipped,
        "seconds": attempt.seconds,
    }


# --------------------------------------------------------------------------- #
# pdf_inspect
# --------------------------------------------------------------------------- #


def pdf_inspect(args: dict, **kwargs) -> str:
    path_text = (args or {}).get("path")
    if not path_text:
        return _fail("bad_request", "I need a 'path' pointing at a PDF.")

    path = _resolve(path_text)
    problem = _check_pdf(path)
    if problem:
        return _fail("not_a_pdf", problem, path=str(path))

    try:
        engine = _engine()
    except DependencyMissing as exc:
        return _missing_dependency(exc)
    try:
        report = engine.inspect_pdf(path)
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        if name in {"PasswordError", "PdfError"} and "password" in str(exc).lower():
            return _fail(
                "password_protected",
                f"{path.name} is password-protected, so I cannot open it. "
                "Remove the password first (Preview → File → Export as PDF, or "
                "`qpdf --decrypt`), then try again.",
                path=str(path),
            )
        return _fail(
            "unreadable",
            f"I could not read {path.name}: {name}: {exc}",
            path=str(path),
        )

    payload = {
        "ok": True,
        "status": "inspected",
        "path": str(path),
        "bytes": report.bytes,
        "size": engine.human_size(report.bytes),
        "pages": report.pages,
        "pdf_version": report.pdf_version,
        "encrypted": bool(report.encrypted),
        "linearized": bool(report.linearized),
        "fonts": report.fonts,
        "attachments": {
            "count": report.attachments,
            "bytes": report.attachment_bytes,
            "size": engine.human_size(report.attachment_bytes),
        },
        "images": {
            "count": len(report.images),
            "bytes": report.image_bytes,
            "size": engine.human_size(report.image_bytes),
            "share_percent": round(report.image_share * 100, 1),
            "max_dpi": report.max_dpi,
            "duplicates": report.duplicates,
            "largest": [
                {
                    "pixels": f"{record.width}x{record.height}",
                    "size": engine.human_size(record.raw_bytes),
                    "dpi": round(record.effective_dpi or 0),
                    "encoding": ",".join(
                        name.lstrip("/") for name in record.filter_names
                    )
                    or "raw",
                    "pages": len(record.pages),
                }
                for record in report.biggest(3)
            ],
        },
        "seconds": report.elapsed,
    }

    verdict, headline, advice = _verdict(engine, report)
    payload["verdict"] = verdict
    payload["headline"] = headline
    payload["advice"] = advice
    payload["message"] = f"{headline} {advice}".strip()
    return json.dumps(payload, ensure_ascii=False)


def _verdict(engine, report):
    human = engine.human_size
    attachment_share = (
        report.attachment_bytes / report.bytes if report.bytes else 0.0
    )
    image_share = report.image_share

    if attachment_share >= 0.25 and report.attachment_bytes > 500_000:
        return (
            "attachments_dominate",
            f"{human(report.attachment_bytes)} of this {human(report.bytes)} file is "
            f"{report.attachments} embedded attachment(s), not pages.",
            "Shrinking the pages will not help much; ask whether those attachments "
            "can be dropped (pdf_shrink with drop_attachments=true).",
        )

    if report.bytes <= 1_000_000:
        return (
            "already_small",
            f"{human(report.bytes)} across {report.pages} page(s) — already small.",
            "Most limits will accept this as it is.",
        )

    if image_share >= 0.6:
        dpi_text = (
            f"the largest are around {report.max_dpi} DPI"
            if report.max_dpi >= 150
            else f"the images are only about {report.max_dpi} DPI"
        )
        if report.max_dpi >= 150:
            advice = (
                "Images are worth re-encoding: dropping them to 110-150 DPI should "
                "cut this by 5-10x with no readable loss. Use pdf_shrink."
            )
        else:
            advice = (
                "The images are already low resolution, so there is little safe "
                "room left. Expect a small gain at best."
            )
        return (
            "images_dominate",
            f"{image_share * 100:.0f}% of this file is {len(report.images)} image(s) "
            f"and {dpi_text}.",
            advice,
        )

    if image_share < 0.2 and report.fonts > 0:
        return (
            "vector_or_text",
            f"Only {image_share * 100:.0f}% of this file is images — the page "
            f"content itself ({report.fonts} font(s), vector drawing) is what you "
            "are carrying.",
            "Re-encoding images would barely move the number, so pdf_shrink will "
            "report that the target is out of reach rather than degrade the file.",
        )

    return (
        "mixed",
        f"Images are {image_share * 100:.0f}% of this file across "
        f"{len(report.images)} object(s).",
        "pdf_shrink can try; the result will show how much of the target is reachable.",
    )


# --------------------------------------------------------------------------- #
# pdf_shrink
# --------------------------------------------------------------------------- #


def pdf_shrink(args: dict, **kwargs) -> str:
    args = args or {}
    path_text = args.get("path")
    target_text = args.get("target_size")

    if not path_text:
        return _fail("bad_request", "I need a 'path' pointing at the PDF to shrink.")
    if target_text in (None, ""):
        return _fail(
            "bad_request",
            "I need a 'target_size' — for example '2 MB' or '500 KB'.",
        )

    try:
        engine = _engine()
    except DependencyMissing as exc:
        return _missing_dependency(exc)

    try:
        target_bytes = engine.parse_size(target_text)
    except ValueError as exc:
        return _fail(
            "bad_size",
            f"I could not read {target_text!r} as a size ({exc}). Try '2 MB', "
            "'500 KB' or a byte count.",
        )

    path = _resolve(path_text)
    problem = _check_pdf(path)
    if problem:
        return _fail("not_a_pdf", problem, path=str(path))

    output_path = args.get("output_path")
    destination = _resolve(output_path) if output_path else None
    if destination is not None and destination == path:
        return _fail(
            "bad_request",
            "The output path is the same as the source. PDF Shrink never "
            "overwrites the original — pick another name.",
        )

    aggressive = bool(args.get("aggressive", False))
    drop_attachments = bool(args.get("drop_attachments", False))
    show_preview = args.get("show_preview", True)
    if isinstance(show_preview, str):
        show_preview = show_preview.strip().lower() not in {"false", "0", "no", ""}

    try:
        result = engine.shrink_to_target(
            path,
            target_bytes,
            destination=destination,
            aggressive=aggressive,
            drop_attachments=drop_attachments,
        )
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        if name == "PasswordError" or "password" in str(exc).lower():
            return _fail(
                "password_protected",
                f"{path.name} is password-protected, so I cannot open it. Remove "
                "the password first, then try again.",
                path=str(path),
            )
        if name == "PdfError":
            return _fail(
                "unreadable",
                f"{path.name} looks damaged: {exc}. The original is untouched.",
                path=str(path),
            )
        return _fail(
            "failed",
            f"Shrinking {path.name} failed: {name}: {exc}. The original is untouched.",
            path=str(path),
        )

    payload = _shrink_payload(engine, result, target_text)
    if show_preview and result.output and result.output != result.source:
        payload["preview_image"] = _render_preview(
            engine, result, Path(payload["output"]["path"])
        )
    return json.dumps(payload, ensure_ascii=False)


def _shrink_payload(engine, result, target_text: str) -> dict:
    original = {
        "path": str(result.source),
        "bytes": result.source_bytes,
        "size": engine.human_size(result.source_bytes),
        "sha256": _short_sha(result.source_sha256),
        "untouched": bool(result.source_untouched),
    }
    target = {"text": str(target_text), "bytes": result.target_bytes,
              "size": engine.human_size(result.target_bytes)}
    ladder = [_describe_attempt(engine, attempt) for attempt in result.attempts]

    if result.reason == "already_small_enough":
        return {
            "ok": True,
            "status": "already_small_enough",
            "target": target,
            "original": original,
            "output": None,
            "pages": result.pages,
            "ladder": [],
            "seconds": result.seconds,
            "message": (
                f"Already {engine.human_size(result.source_bytes)} — that is under "
                f"your {target['size']} limit, so no copy was made."
            ),
        }

    output = None
    if result.output is not None:
        output = {
            "path": str(result.output),
            "bytes": result.output_bytes,
            "size": engine.human_size(result.output_bytes),
            "name": Path(result.output).name,
            "fits": bool(result.ok),
        }

    verified = {
        "reopens": bool(result.verification.get("opens")),
        "page_count_matches": bool(result.verification.get("pages_equal")),
        "blank_pages": result.verification.get("blank_pages", 0),
        "pages": result.verification.get("pages", 0),
    }

    if result.ok:
        chosen = result.chosen
        images = (
            "no image was re-encoded (structure cleanup only)"
            if chosen is not None and chosen.preset.lossless
            else (
                f"{chosen.images_downsampled} image(s) re-encoded at "
                f"{chosen.preset.describe()}"
                if chosen is not None
                else "no image was re-encoded"
            )
        )
        message = (
            f"{original['size']} → {output['size']} "
            f"({result.saved_percent:.1f}% smaller). Saved as {output['name']}. "
            f"{images}. Original untouched (sha-256 checked)."
        )
        if verified["reopens"]:
            message += (
                f" Re-opened the copy: {verified['pages']} pages, "
                f"{verified['blank_pages']} blank."
            )
        return {
            "ok": True,
            "status": "fits",
            "target": target,
            "original": original,
            "output": output,
            "saved": {
                "bytes": result.saved_bytes,
                "size": engine.human_size(result.saved_bytes),
                "percent": round(result.saved_percent, 1),
            },
            "pages": result.pages,
            "quality": None if result.chosen is None else {
                "preset": result.chosen.preset.name,
                "description": result.chosen.preset.describe(),
                "images_downsampled": result.chosen.images_downsampled,
                "images_skipped": result.chosen.images_skipped,
            },
            "verified": verified,
            "ladder": ladder,
            "seconds": result.seconds,
            "message": message,
        }

    # Honest miss: the target is out of reach without wrecking the document.
    floor = result.floor
    if floor is None or output is None:
        return {
            "ok": False,
            "status": "no_gain",
            "target": target,
            "original": original,
            "output": None,
            "pages": result.pages,
            "ladder": ladder,
            "seconds": result.seconds,
            "message": (
                f"Nothing safe to remove: {engine.human_size(result.source_bytes)} "
                "of this file is not re-encodable image data, so shrinking would "
                "not move the number. The original is untouched."
            ),
            "suggestions": [
                "Check what is inside with pdf_inspect.",
                "If the limit is a hard one, the pages may need to be split or "
                "re-exported from the source document.",
            ],
        }

    suggestions = [
        f"Raise the target to about {_suggest_target(result.output_bytes)} and keep "
        "this quality.",
    ]
    if not result.aggressive:
        suggestions.append(
            "Call again with aggressive=true to allow 72 DPI (rougher, smaller)."
        )
    message = (
        f"I could not get this under {target['size']} without making it "
        f"unreadable. The smallest safe copy is {output['size']} "
        f"({floor.preset.describe()}) — saved as {output['name']}. "
        f"That is {result.saved_percent:.1f}% smaller than the original, which is "
        f"untouched. To go lower: {suggestions[0].lower()}"
    )
    if not result.aggressive:
        message += " Or allow aggressive quality."

    return {
        "ok": False,
        "status": "target_unreachable",
        "target": target,
        "original": original,
        "output": output,
        "smallest_safe": {
            "size": output["size"],
            "bytes": result.output_bytes,
            "quality": floor.preset.describe(),
            "preset": floor.preset.name,
        },
        "pages": result.pages,
        "quality": {
            "preset": floor.preset.name,
            "description": floor.preset.describe(),
            "images_downsampled": floor.images_downsampled,
            "images_skipped": floor.images_skipped,
        },
        "verified": verified,
        "ladder": ladder,
        "suggestions": suggestions,
        "seconds": result.seconds,
        "message": message,
    }


def _render_preview(engine, result, output_path: Path) -> str | None:
    try:
        preview_module = _preview()
        if not preview_module.available():
            return None
        sheet_path = (
            _data_dir()
            / "previews"
            / f"{result.source.stem}-{output_path.stem}.jpg"
        )
        chosen = result.chosen
        caption = (
            f"{result.pages} pages · {chosen.preset.describe()}"
            if chosen is not None
            else f"{result.pages} pages"
        )
        sheet = preview_module.comparison_sheet(
            result.source,
            output_path,
            sheet_path,
            original_label=engine.human_size(result.source_bytes),
            compressed_label=engine.human_size(result.output_bytes),
            caption=caption,
        )
        return str(sheet) if sheet else None
    except Exception:  # noqa: BLE001 - a missing picture never fails the job
        return None
