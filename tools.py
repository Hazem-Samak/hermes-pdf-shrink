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


def _plural(count: int, word: str) -> str:
    return f"{count} {word}" if count == 1 else f"{count} {word}s"


def _saved_label(output: dict, original: dict) -> str:
    """Name the output; include its folder when it is not beside the source."""
    out_path = Path(output["path"])
    source_path = Path(original["path"])
    if out_path.parent == source_path.parent:
        return output["name"]
    return output["path"]


def _suggest_target(floor_bytes: int, source_bytes: int) -> str | None:
    """A reachable round target above the honest floor, or None.

    The number is rendered with the same precision the tool prints sizes at, and
    then rounded up to that precision so parsing the suggestion back yields at
    least the floor. A suggestion that displays as "1.13 MB" must not mean
    1,130,000 bytes when the floor copy is 1,132,128 bytes.
    """
    engine = _engine()
    if floor_bytes >= 100_000_000:
        quantum = 1_000_000
    elif floor_bytes >= 1_000_000:
        quantum = 10_000
    elif floor_bytes >= 1_000:
        quantum = 10
    else:
        quantum = 1

    rounded = ((floor_bytes + quantum - 1) // quantum) * quantum
    if rounded < 1_000_000:
        rounded = 1_000_000
    try:
        while engine.parse_size(engine.human_size(rounded)) < floor_bytes:
            rounded += quantum
    except ValueError:
        return None
    if rounded >= source_bytes:
        return None
    return engine.human_size(rounded)


def _describe_attempt(engine, attempt) -> dict:
    return {
        "preset": attempt.preset.name,
        "quality": attempt.describe(),
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
        "reclaimable": {
            "count": len(report.reclaimable),
            "bytes": report.reclaimable_bytes,
            "size": engine.human_size(report.reclaimable_bytes),
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
        reclaimable_share = (
            report.reclaimable_bytes / report.image_bytes if report.image_bytes else 0.0
        )
        if reclaimable_share < 0.1:
            return (
                "images_not_reclaimable",
                f"{image_share * 100:.0f}% of this file is {len(report.images)} image(s), "
                f"but only {human(report.reclaimable_bytes)} is in a form this tool "
                "can safely re-encode.",
                "Those images are masked, CMYK, non-8-bit, /Decode or already compressed, "
                "and PDF Shrink leaves them alone rather than risk corrupting them. "
                "Re-exporting them as plain RGB/grayscale images would make them "
                "shrinkable.",
            )
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

    if image_share < 0.2 or len(report.images) == 0:
        if not report.images:
            headline = (
                f"No images at all: this file is {report.pages} page(s) of "
                f"{'text and fonts' if report.fonts else 'vector drawing'}, "
                f"plus structure."
            )
        else:
            headline = (
                f"Only {image_share * 100:.0f}% of this file is images — the page "
                f"content itself ({report.fonts} font(s), vector drawing) is what "
                "you are carrying."
            )
        return (
            "vector_or_text",
            headline,
            "Re-encoding images would barely move the number, so pdf_shrink will "
            "still do a lossless structure pass and then report that the target is "
            "out of reach rather than degrade the file.",
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
    except engine.DestinationExists as exc:
        return _fail(
            "destination_exists",
            f"{exc}. Choose a different output name; nothing was written and "
            "the original was not written to.",
            path=str(path),
            output_path=str(destination) if destination else None,
        )
    except engine.SourceOverwrite:
        return _fail(
            "bad_request",
            "The output path is the source file. PDF Shrink never overwrites "
            "the original — pick another name.",
            path=str(path),
        )
    except engine.DestinationNotWritable as exc:
        return _fail(
            "destination_not_writable",
            f"{exc}. Nothing was written and the original was not written to.",
            path=str(path),
            output_path=str(destination) if destination else None,
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
                f"{path.name} looks damaged: {exc}. PDF Shrink did not write to "
                "the original.",
                path=str(path),
            )
        return _fail(
            "failed",
            f"Shrinking {path.name} failed: {name}: {exc}. PDF Shrink did not "
            "write to the original.",
            path=str(path),
        )

    payload = _shrink_payload(engine, result, target_text)
    if (
        show_preview
        and result.verified
        and result.output
        and result.output != result.source
    ):
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
    inspection = result.inspection
    dropped = None
    if result.drop_attachments and inspection is not None and inspection.attachments:
        dropped = {
            "count": inspection.attachments,
            "bytes": inspection.attachment_bytes,
            "size": engine.human_size(inspection.attachment_bytes),
        }

    output = None
    if result.output is not None:
        output = {
            "path": str(result.output),
            "bytes": result.output_bytes,
            "size": engine.human_size(result.output_bytes),
            "name": Path(result.output).name,
            "fits": bool(result.fits),
        }

    verified = {
        "reopens": bool(result.verification.get("opens")),
        "page_count_matches": bool(result.verification.get("pages_equal")),
        "blank_pages": result.verification.get("blank_pages", 0),
        "syntax_errors": result.verification.get("syntax_errors", 0),
        "attachments": result.verification.get("attachments"),
        "pages": result.verification.get("pages", 0),
    }

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

    if result.reason == "source_changed":
        message = (
            "The source file changed while I was working, so I cannot claim it "
            "was left untouched."
        )
        if output is not None:
            message += (
                f" The copy at {_saved_label(output, original)} was built from the original I "
                "opened at the start; re-check it before using it."
            )
        else:
            message += " No copy was delivered."
        return {
            "ok": False,
            "status": "source_changed",
            "target": target,
            "original": original,
            "output": output,
            "pages": result.pages,
            "ladder": ladder,
            "verified": verified,
            "seconds": result.seconds,
            "message": message,
        }

    if result.reason == "verification_failed":
        detail = result.verification.get("error")
        if not detail and verified["syntax_errors"]:
            detail = f"{verified['syntax_errors']} structural problem(s)"
        if not detail and verified["blank_pages"]:
            detail = f"{verified['blank_pages']} blank page(s)"
        if not detail and not verified["page_count_matches"]:
            detail = "the page count did not match"
        detail = f" ({detail})" if detail else ""
        if output is not None:
            message = (
                f"I wrote {output['size']} to {_saved_label(output, original)}, but it failed the "
                f"re-open check{detail}. Do not rely on that copy. The original "
                "is untouched."
            )
        else:
            message = (
                f"The best copy I built failed the re-open check{detail}, so I did "
                "not hand it over. The original is untouched."
            )
        return {
            "ok": False,
            "status": "verification_failed",
            "target": target,
            "original": original,
            "output": output,
            "pages": result.pages,
            "ladder": ladder,
            "verified": verified,
            "seconds": result.seconds,
            "message": message,
        }

    if result.ok and output is not None:
        chosen = result.chosen
        skipped = chosen.images_skipped if chosen is not None else 0
        if chosen is not None and chosen.preset.lossless:
            images = "no image was re-encoded (structure cleanup only)"
        elif chosen is not None and chosen.images_downsampled == 0:
            images = "no image needed re-encoding (structure cleanup only)"
            if skipped:
                images += f"; {_plural(skipped, 'image')} left alone"
        elif chosen is not None:
            images = (
                f"{_plural(chosen.images_downsampled, 'image')} re-encoded at "
                f"{chosen.describe()}"
            )
            if skipped:
                images += f", {_plural(skipped, 'image')} left alone"
        else:
            images = "no image was re-encoded"
        message = (
            f"{original['size']} → {output['size']} "
            f"({result.saved_percent:.1f}% smaller). Saved as {_saved_label(output, original)}. "
            f"{images}. Original untouched (sha-256 checked)."
        )
        if verified["reopens"]:
            blank = verified["blank_pages"]
            message += (
                f" Re-opened the copy: {verified['pages']} pages, "
                f"{'none blank' if not blank else str(blank) + ' blank'}."
            )
        if dropped and verified.get("attachments") == 0:
            message += (
                f" Dropped {_plural(dropped['count'], 'embedded attachment')} "
                f"({dropped['size']})."
            )
        return {
            "ok": True,
            "status": "fits",
            "target": target,
            "original": original,
            "output": output,
            "dropped_attachments": dropped,
            "saved": {
                "bytes": result.saved_bytes,
                "size": engine.human_size(result.saved_bytes),
                "percent": round(result.saved_percent, 1),
            },
            "pages": result.pages,
            "quality": None if result.chosen is None else {
                "preset": result.chosen.preset.name,
                "description": result.chosen.describe(),
                "images_downsampled": result.chosen.images_downsampled,
                "images_skipped": result.chosen.images_skipped,
            },
            "verified": verified,
            "ladder": ladder,
            "seconds": result.seconds,
            "message": message,
        }

    # Honest miss.
    floor = result.floor
    attachments = inspection.attachments if inspection is not None else 0
    attachment_bytes = inspection.attachment_bytes if inspection is not None else 0
    reclaimable = inspection.reclaimable_bytes if inspection is not None else 0

    if floor is None or output is None:
        if inspection is not None and inspection.images and reclaimable == 0:
            reason = (
                "Nothing here is an image this tool can safely re-encode "
                "(they are masked, CMYK, non-8-bit, /Decode or already compressed), so "
                "the file cannot be made smaller without changing what it contains."
            )
        elif result.chosen is not None and result.chosen.bytes >= result.source_bytes:
            reason = (
                "Every safe setting came out at or above the original size "
                f"(best: {engine.human_size(result.chosen.bytes)}), so there is "
                "nothing worth handing over."
            )
        elif result.chosen is not None:
            gain = result.source_bytes - result.chosen.bytes
            percent = 100.0 * gain / result.source_bytes if result.source_bytes else 0
            reason = (
                "The best safe copy is only "
                f"{engine.human_size(result.chosen.bytes)} "
                f"({percent:.1f}% smaller), which is not worth writing another "
                "file for."
            )
        else:
            reason = "No safe setting produced a copy smaller than the original."
        suggestions = ["Check what is inside with pdf_inspect."]
        if not result.drop_attachments and attachments and attachment_bytes > 200_000:
            suggestions.append(
                f"Drop {_plural(attachments, 'embedded attachment')} "
                f"({engine.human_size(attachment_bytes)}) with "
                "drop_attachments=true if losing them is acceptable."
            )
        suggestions.append(
            "If the limit is a hard one, the pages may need to be split or "
            "re-exported from the source document."
        )
        return {
            "ok": False,
            "status": "no_gain",
            "target": target,
            "original": original,
            "output": None,
            "pages": result.pages,
            "ladder": ladder,
            "seconds": result.seconds,
            "message": f"{reason} The original is untouched.",
            "suggestions": suggestions,
        }

    suggested = _suggest_target(result.output_bytes, result.source_bytes)
    suggestions = []
    if suggested:
        suggestions.append(
            f"Raise the target to about {suggested} and keep this quality."
        )
    aggressive_helps = (
        not result.aggressive
        and floor.preset.dpi is not None
        and floor.preset.name != engine.AGGRESSIVE_LADDER[-1].name
        and (reclaimable > 0 or floor.images_downsampled > 0)
    )
    if aggressive_helps:
        suggestions.append(
            "Call again with aggressive=true to allow 72 DPI (rougher, smaller)."
        )
    if not result.drop_attachments and attachments and attachment_bytes > 200_000:
        suggestions.append(
            f"Drop {_plural(attachments, 'embedded attachment')} "
            f"({engine.human_size(attachment_bytes)}) with drop_attachments=true "
            "if losing them is acceptable."
        )
    if not suggestions:
        suggestions.append(
            "Re-exporting or splitting the pages may be the only way to go lower."
        )

    if floor.preset.lossless:
        opening = (
            f"I could not get this under {target['size']} without changing the "
            "document."
        )
    else:
        opening = (
            f"I could not get this under {target['size']} without dropping below "
            "the quality this run allowed."
        )
    message = (
        f"{opening} The smallest safe copy is {output['size']} "
        f"({floor.describe()}) — saved as {_saved_label(output, original)}. "
        f"That is {result.saved_percent:.1f}% smaller than the original, which is "
        "untouched."
    )
    if suggested:
        message += f" To go lower, raise the target to about {suggested}"
        message += (
            ", or call again with aggressive=true for 72 DPI."
            if aggressive_helps
            else "."
        )
    elif aggressive_helps:
        message += (
            " To go lower, call again with aggressive=true for 72 DPI."
        )
    if dropped and verified.get("attachments") == 0:
        message += (
            f" Dropped {_plural(dropped['count'], 'embedded attachment')} "
            f"({dropped['size']})."
        )

    return {
        "ok": False,
        "status": "target_unreachable",
        "target": target,
        "original": original,
        "output": output,
        "dropped_attachments": dropped,
        "smallest_safe": {
            "size": output["size"],
            "bytes": result.output_bytes,
            "quality": floor.describe(),
            "preset": floor.preset.name,
        },
        "pages": result.pages,
        "quality": {
            "preset": floor.preset.name,
            "description": floor.describe(),
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
            f"{result.pages} pages · {chosen.describe()}"
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
