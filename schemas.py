"""Tool schemas — what the model reads before deciding to call them."""

PDF_SHRINK = {
    "name": "pdf_shrink",
    "description": (
        "Shrink a PDF so it fits under a size limit (for example 2 MB), working "
        "entirely on this machine — no upload, no API key. Writes a NEW file and "
        "never modifies the original. Images are downsampled on a measured "
        "quality ladder until the file fits; the best quality that still fits "
        "wins. If the limit cannot be reached without making the document "
        "unreadable, the tool says so, reports the smallest safe size, and "
        "saves that copy instead of ruining the document. Returns JSON with the "
        "output path, before/after sizes, the quality used, and a message ready "
        "to show the user."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": (
                    "Path to the source PDF. Absolute paths are best; '~' is "
                    "expanded. The file is only ever read."
                ),
            },
            "target_size": {
                "type": "string",
                "description": (
                    "The size limit to fit under, e.g. '2 MB', '500 KB', '1.5 mb', "
                    "or a byte count. Decimal units, the way upload forms quote "
                    "them: 2 MB = 2,000,000 bytes."
                ),
            },
            "output_path": {
                "type": "string",
                "description": (
                    "Where to write the compressed copy. Optional — the default is "
                    "next to the original, named '<name>-compressed.pdf'."
                ),
            },
            "aggressive": {
                "type": "boolean",
                "description": (
                    "Allow the last-resort setting (72 DPI / JPEG quality 36). "
                    "Only use when the user would rather have a small file than a "
                    "crisp one — scanned text gets noticeably rough below 88 DPI."
                ),
            },
            "drop_attachments": {
                "type": "boolean",
                "description": (
                    "Also remove files embedded inside the PDF (attachments such "
                    "as spreadsheets or archives). Only when the user agrees to "
                    "lose them; the report names how much this recovers."
                ),
            },
            "show_preview": {
                "type": "boolean",
                "description": (
                    "Render a before/after comparison image of page 1 so the user "
                    "can judge quality. Default true."
                ),
            },
        },
        "required": ["path", "target_size"],
    },
}


PDF_INSPECT = {
    "name": "pdf_inspect",
    "description": (
        "Report why a PDF is large and how much it can shrink, without changing "
        "anything. Lists the biggest images, their effective print resolution, "
        "fonts, embedded attachments and duplicates, then gives a verdict "
        "(images dominate / vector-heavy / already small). Use it before "
        "pdf_shrink when the user asks 'why is this file so big?' or 'can this "
        "get to 2 MB?'."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Path to the PDF to survey. Read-only.",
            }
        },
        "required": ["path"],
    },
}
