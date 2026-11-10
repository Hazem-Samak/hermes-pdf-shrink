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
2. What the smallest safe size is, and where that copy was saved (if `output` is
   present).
3. The levers that are actually in `suggestions` — normally a higher target;
   `aggressive=true` is listed only when it can help. Do not invent a lever that
   is not there.

Do not claim success, and do not hand over a file that only "fits" if the tool
says it fits. `ok: false` means the copy that exists is the honest floor, not the
goal.

If `status` is `"no_gain"`, no copy was written: the best safe attempt was not
meaningfully smaller. Say that, not "it failed".

## Things that matter

- **The original is never modified.** The tool writes `<name>-compressed.pdf`
  beside it when that folder is writable, otherwise in the current working
  directory, or to `output_path`. Never suggest overwriting the source. If the
  output path already exists the call returns `status: "destination_exists"`;
  choose a different name, do not retry with the same one. If it returns
  `"destination_not_writable"`, the folder is not writable, the name does not
  end in `.pdf`, or the location is one Hermes's write denylist refuses — ask
  the user for a `.pdf` path in a folder they can use.
  Temporary rungs stay in the system temp directory, not beside the PDF.
- **Quality is chosen by measurement.** The ladder starts at the best setting and
  only gets rougher, rebuilt each time from the pristine original — so the copy
  you get is the mildest setting that fits, not the smallest file possible.
- **Scanned PDFs shrink most** (images are 90%+ of the file). Text-and-vector
  PDFs barely move; if `pdf_inspect` says `vector_or_text` or
  `images_not_reclaimable`, tell the user that up front instead of burning a
  minute on a shrink that cannot work.
- **Password-protected PDFs are refused.** Say so and suggest removing the
  password (Preview → Export as PDF, or `qpdf --decrypt in.pdf out.pdf`).
- **Attachments.** If `pdf_inspect` shows big embedded attachments, ask before
  calling `pdf_shrink(drop_attachments=true)` — those files are lost.
- **A big file takes a while** (a 40-page scan is a few seconds; a 300 MB file can
  take a minute). Say you are working on it rather than calling twice.

## Verify before you brag

The JSON carries `verified.reopens`, `verified.page_count_matches` and
`verified.syntax_errors`. Only say "checked" when `reopens` and
`page_count_matches` are true and `syntax_errors` is zero. If `blank_pages` is
above zero, tell the user the copy needs a look and point at `preview_image`.

If `status` is `"verification_failed"`, the copy did not pass those checks: say
not to rely on it. If it is `"source_changed"`, the original changed while the
tool worked; say that plainly and do not claim it was untouched.
