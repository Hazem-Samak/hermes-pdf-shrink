---
name: pdf-shrink
description: "Shrink a PDF under a size limit locally; report honestly when impossible."
---

# PDF Shrink

Use this when someone needs a PDF to fit a size limit — an email attachment, a
visa portal, a grant system, an "attachment must be under 2 MB" form.

The work happens with the `pdf_shrink` tool. Your job is to pick the right call,
then report the result in plain language.

## The three calls

| Situation | Call |
| --- | --- |
| "Make this fit under 2 MB" | `pdf_shrink(path=..., target_size="2 MB")` |
| "Why is this file so big?" / "Can it even get that small?" | `pdf_inspect(path=...)` first — it costs nothing and changes nothing |
| "It has to be 500 KB and I don't care how it looks" | `pdf_shrink(..., aggressive=true)` |

`target_size` accepts `"2 MB"`, `"500 KB"`, `"1.5 mb"`, `"950000"`. Units are
decimal, matching upload forms: 2 MB is 2,000,000 bytes.

## Reporting the result

Read the JSON, then use its `message` field as the backbone of your reply — it
already has the sizes, the quality used and whether the original was touched. Add
one sentence of context (where the file is, whether it fits their limit) and stop.

Never restate the whole JSON. The user wants: did it fit, what is the file called,
what did it cost in quality.

**Show the preview.** When the JSON has a `preview_image` path, include it on its
own line as `MEDIA:<path>`. That renders the before/after comparison inline and is
the fastest way for the user to judge whether the quality is acceptable.

## When it does not fit (`status: "target_unreachable"`)

This is not a failure to hide — it is the point of the tool. Say plainly:

1. The target was not reachable **without making it unreadable**.
2. What the smallest safe size is, and where that copy was saved.
3. The two levers: raise the target to the `suggestions` value, or re-run with
   `aggressive=true` (72 DPI — noticeably rough on scanned text).

Do not claim success, and do not hand over a file that only "fits" if the tool
says it fits. `ok: false` means the copy that exists is the honest floor, not the
goal.

## Things that matter

- **The original is never modified.** The tool writes `<name>-compressed.pdf`
  beside it, or to `output_path`. Never suggest overwriting the source.
- **Quality is chosen by measurement.** The ladder starts at the best setting and
  only gets rougher, rebuilt each time from the pristine original — so the copy
  you get is the mildest setting that fits, not the smallest file possible.
- **Scanned PDFs shrink most** (images are 90%+ of the file). Text-and-vector
  PDFs barely move; if `pdf_inspect` says `vector_or_text`, tell the user that up
  front instead of burning a minute on a shrink that cannot work.
- **Password-protected PDFs are refused.** Say so and suggest removing the
  password (Preview → Export as PDF, or `qpdf --decrypt in.pdf out.pdf`).
- **Attachments.** If `pdf_inspect` shows big embedded attachments, ask before
  calling `pdf_shrink(drop_attachments=true)` — those files are lost.
- **A big file takes a while** (a 40-page scan is a few seconds; a 300 MB file can
  take a minute). Say you are working on it rather than calling twice.

## Verify before you brag

The JSON carries `verified.reopens` and `verified.page_count_matches`. Only say
"checked" when those are true. If `verified.blank_pages` is above zero, tell the
user the copy needs a look and point at `preview_image`.
