"""PDF Shrink — hit a size limit without wrecking the document.

Registers two tools and one bundled skill. Nothing heavy is imported here: the
engine (pikepdf, PDFium) loads on first tool call, so installing or disabling the
plugin never slows Hermes down and a missing dependency surfaces as a clear tool
error rather than a plugin that refuses to load.
"""

from __future__ import annotations

import logging
from pathlib import Path

from . import schemas, tools

logger = logging.getLogger(__name__)

TOOLSET = "pdf"


def register(ctx) -> None:
    ctx.register_tool(
        name="pdf_shrink",
        toolset=TOOLSET,
        schema=schemas.PDF_SHRINK,
        handler=tools.pdf_shrink,
        description="Shrink a PDF to a target size, locally, without touching the original",
        emoji="🗜️",
    )
    ctx.register_tool(
        name="pdf_inspect",
        toolset=TOOLSET,
        schema=schemas.PDF_INSPECT,
        handler=tools.pdf_inspect,
        description="Explain why a PDF is large and how far it can shrink",
        emoji="🔎",
    )

    skills_dir = Path(__file__).parent / "skills"
    if skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            skill_md = child / "SKILL.md"
            if child.is_dir() and skill_md.exists():
                try:
                    ctx.register_skill(child.name, skill_md)
                except Exception as exc:  # noqa: BLE001 - never fail the plugin for a skill
                    logger.warning("pdf-shrink: could not register skill %s: %s", child.name, exc)
