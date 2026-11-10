# PDF Shrink — a Hermes plugin

**Hit the size limit without wrecking the document.**

Give Hermes a PDF and a target — *"get this under 2 MB"* — and it writes a copy
that fits. Everything runs on your machine: no upload, no account, no API key, no
"free online compressor" holding your contract for 24 hours.

```
29.13 MB  →  1.69 MB     24 pages · 88 DPI · JPEG quality 48
original untouched · copy re-opened and checked · ~3 s
```

Every run writes a **new** file. If the named output already exists, the tool
refuses rather than replacing it; the original is hashed before and after and the
result is reported even when that check fails. Ladder rungs are built in a
private system-temp directory and deleted, never beside your PDF. If the source
folder is read-only, the default copy goes to the current working directory and
the reported path says where; pass `output_path` to choose.

## Why another PDF compressor

Every web compressor does the same thing badly: it picks one aggressive preset,
applies it to everything, and hands back whatever comes out. If the number still
does not fit, it says nothing useful.

PDF Shrink does four things differently.

**1. It solves for quality, not for the smallest file.** Each image that can be
re-encoded safely is downsampled to the resolution it is *actually displayed at*
— recovered by replaying the page's content stream (`q`/`Q`/`cm`/`Do`), including
images drawn inside nested Form XObjects — then a measured ladder of DPI/quality
settings is tried from the mildest rung down. The first rung that fits wins, so
you get the best-looking file that meets the limit. A logo printed one inch wide
is never forced to full-page resolution.

**2. Every rung is rebuilt from the pristine original.** A strong setting never
inherits a weaker setting's damage, so the copy is one generation of compression,
not six.

**3. It tells you the truth when it cannot.** Below about 88 DPI, scanned A4 text
stops being dependable. Rather than hand you mush that technically fits, the tool
stops, reports the smallest safe size, saves *that* when it is meaningfully
smaller, and says what your options are. `aggressive=true` exists for people who
genuinely prefer a small file to a readable one.

**4. It proves the result.** The copy is reopened, page count and blank pages are
checked, and its structure is syntax-checked before delivery. The source is
re-hashed to confirm it was never touched. When a copy is written and PDFium is
available, a before/after render of page 1 comes back too, so quality is a thing
you look at, not a thing you trust.

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
python -m pytest tests -q                         # 48 tests, no fixtures
scripts/build_demo.sh                             # regenerate demo + catalog assets
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

Images that cannot be re-encoded safely are left byte-for-byte alone: `JBIG2`,
`CCITT` fax encodings, anything that is not 8-bit, soft or stencil masks, images
carrying a `/Mask`, `/Matte` or `/Decode` array, CMYK and other print colour
spaces, and tiny logos. Re-encoding those without carrying their mask or colour profile through
the same transform is how images come back transparent, black or inverted, so the
tool declines instead. Text, fonts and vector art are never rasterised: a scanned
report stays a scanned report, and a digital document stays selectable and
searchable.

## Honest limits

- **Text-and-vector PDFs barely shrink.** A 20 MB CAD export is 20 MB of drawing,
  not photos. `pdf_inspect` says so up front, and `pdf_shrink` reports the miss
  instead of degrading the file.
- **Masked and CMYK images are not resampled.** The tool leaves them alone rather
  than risk a broken mask or a colour shift; if they dominate the file it says so
  and suggests re-exporting them as plain RGB/grayscale.
- **It never overwrites a file.** The default name gets a numeric suffix if it
  already exists; an explicit `output_path` that exists is refused with
  `destination_exists`.
- **The output name is confined.** An explicit `output_path` must end in `.pdf`,
  and its folder must be one Hermes's own file tools would write to: the write
  denylist that keeps the agent out of `.ssh`, credential files and the like is
  applied here too. Either refusal returns `destination_not_writable` before
  anything is opened or written, and it outranks `destination_exists` for a path
  that is refused and already exists.
- **A read-only source folder is fine.** Temporary rungs live in the system temp
  directory (0700) and are removed on every exit path. The default output tries
  the source folder, then the current working directory; an explicit
  `output_path` is used as given. If none can be written, the call returns
  `destination_not_writable` instead of a traceback.
- **Temp space.** Rungs are built in the system temp directory, which honours
  `TMPDIR`. For a very large PDF, point `TMPDIR` at a filesystem with room.
- **No OCR, no page splitting, no password removal.** It refuses
  password-protected files with a clear message rather than pretending.
- **Attachments are kept** unless you pass `drop_attachments=true`, because those
  are the user's files, not decoration.
- **Scans get raster-heavy results at the aggressive end.** That is what "smaller
  than readable" means; the preview shows it before you commit.

## Privacy

No network calls, no telemetry, no subprocess. The plugin reads the PDF you point
it at, writes one new file (beside it when that folder is writable, otherwise in
the current working directory or wherever `output_path` says), and stores its
preview images under the Hermes plugin data directory. Full-size ladder rungs are
written to a private system-temp directory and removed before the call returns;
nothing is left beside the PDF. That is the whole footprint.

**Disclosure.** `pdf_shrink` reads the PDF you name and writes one new `.pdf`
(beside the original when that folder is writable, otherwise in the current
working directory, or at `output_path`); it never writes to the input, it refuses
to replace an existing file, and it re-hashes the input afterwards to prove it.
Temporary rungs are written to the system temp directory and deleted.
`pdf_inspect` only reads. Comparison images are written to
`<HERMES_HOME>/plugin-data/pdf-shrink/previews/`. Files are re-encoded with
pikepdf/PDFium and the result is re-opened to verify the page count, blank pages
and structural syntax — a copy that cannot be verified is reported as failed, not
handed over as a success. Nothing leaves the machine: no network, no subprocess,
no credentials, no telemetry. The only destructive option is
`drop_attachments=true`, which removes files embedded inside the PDF and is off by
default.

## Licence

MIT. Bundled third-party libraries keep their own licences: pikepdf (MPL-2.0),
PDFium via pypdfium2 (BSD-3-Clause / Apache-2.0), Pillow (MIT-CMU).
