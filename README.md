# PDF Shrink — a Hermes plugin

**Hit the size limit without wrecking the document.**

Give Hermes a PDF and a target — *"get this under 2 MB"* — and it writes a copy
that fits. Everything runs on your machine: no upload, no account, no API key, no
"free online compressor" holding your contract for 24 hours.

```
29.13 MB  →  1.69 MB     24 pages · 88 DPI · JPEG quality 48
original untouched · copy re-opened and checked · 6.2 s
```

## Why another PDF compressor

Every web compressor does the same thing badly: it picks one aggressive preset,
applies it to everything, and hands back whatever comes out. If the number still
does not fit, it says nothing useful.

PDF Shrink does four things differently.

**1. It solves for quality, not for the smallest file.** Each image is
downsampled to the resolution it is *actually displayed at* — recovered by
replaying the page's content stream (`q`/`Q`/`cm`/`Do`) to get the graphics
matrix — then a measured ladder of DPI/quality settings is tried from the mildest
rung down. The first rung that fits wins, so you get the best-looking file that
meets the limit. A logo printed one inch wide is never forced to full-page
resolution.

**2. Every rung is rebuilt from the pristine original.** A strong setting never
inherits a weaker setting's damage, so the copy is one generation of compression,
not six.

**3. It tells you the truth when it cannot.** Below about 88 DPI, scanned A4 text
stops being dependable. Rather than hand you mush that technically fits, the tool
stops, reports the smallest safe size, saves *that*, and says what your options
are. `aggressive=true` exists for people who genuinely prefer a small file to a
readable one.

**4. It proves the result.** The copy is reopened and counted (pages match? any
blank?), and the source is re-hashed to confirm it was never touched. A
before/after render of page 1 comes back with every run, so quality is a thing you
look at, not a thing you trust.

## What it looks like

Two tools:

| Tool | What it answers |
| --- | --- |
| `pdf_inspect` | "Why is this file so big, and can it even get to 2 MB?" — image share, effective DPI, fonts, attachments, duplicates, and a verdict |
| `pdf_shrink` | "Make it fit." — the ladder, the verified copy, the preview image |

Ask in plain language:

> Shrink `~/Downloads/site-report.pdf` to under 2 MB.

> Why is this PDF 48 MB? Can it get to 5 MB?

> This portal only takes 500 KB. I don't care if it looks rough.

## Install

From the repository (works today):

```
hermes plugins install Hazem-Samak/hermes-pdf-shrink --yes-deps
```

From the catalog, once the entry is merged:

```
hermes plugins install pdf-shrink
```

Local development:

```
hermes plugins doctor /path/to/pdf-shrink --ci     # structure + contracts
hermes plugins validate /path/to/pdf-shrink --install-deps   # catalog CI gate
python -m pytest tests -q                         # 30 tests, no fixtures
```

Dependencies (installed by Hermes when you enable the plugin): `pikepdf` for
reading and rewriting PDF structure, `pypdfium2` for the preview render, and
`pillow` for image work.

## How it decides

```
lossless      structure cleanup only — no image is re-encoded
200 DPI q82   ┐
160 DPI q74   │  measured ladder: mildest rung that fits wins
130 DPI q66   │
110 DPI q58   │
 88 DPI q48   ┘  ← the default floor: below this, scanned text stops holding up
 72 DPI q36      opt-in only (aggressive=true)
```

Images that are already efficient (`JBIG2`, `CCITT` fax encodings, 1-bit masks,
tiny logos) are left alone — re-encoding them would make the file bigger and
worse. Text, fonts and vector art are never rasterised: a scanned report stays a
scanned report, and a digital document stays selectable and searchable.

## Honest limits

- **Text-and-vector PDFs barely shrink.** A 20 MB CAD export is 20 MB of drawing,
  not photos. `pdf_inspect` says so up front, and `pdf_shrink` reports the miss
  instead of degrading the file.
- **No OCR, no page splitting, no password removal.** It refuses
  password-protected files with a clear message rather than pretending.
- **Attachments are kept** unless you pass `drop_attachments=true`, because those
  are the user's files, not decoration.
- **Scans get raster-heavy results at the aggressive end.** That is what "smaller
  than readable" means; the preview shows it before you commit.

## Privacy

No network calls, no telemetry, no subprocess. The plugin reads the PDF you point
it at, writes one file next to it (or wherever you ask), and stores its preview
images under the Hermes plugin data directory. That is the whole footprint.

**Disclosure.** `pdf_shrink` reads the PDF you name and writes one new `.pdf`
(beside the original by default, or at `output_path`); it never writes to the
input, and it re-hashes the input afterwards to prove it. `pdf_inspect` only
reads. Comparison images are written to
`<HERMES_HOME>/plugin-data/pdf-shrink/previews/`. Files are re-encoded with
pikepdf/PDFium and the result is re-opened to verify the page count — a copy that
cannot be verified is reported as failed, not handed over as a success. Nothing
leaves the machine: no network, no subprocess, no credentials, no telemetry. The
only destructive option is `drop_attachments=true`, which removes files embedded
inside the PDF and is off by default.

## Licence

MIT. Bundled third-party libraries keep their own licences: pikepdf (MPL-2.0),
PDFium via pypdfium2 (BSD-3-Clause / Apache-2.0), Pillow (MIT-CMU).
