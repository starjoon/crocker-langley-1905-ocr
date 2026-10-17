# Decisions

Why the pipeline is built the way it is. Add new entries at the top with their date;
when a decision is reversed, add a new entry saying so rather than editing the old one.

## 2026-10-16 — Pilot on 5 sample pages

**Crop ads and split columns before any OCR.**
Why: vision models downscale a whole page (~1,700 px wide) until small type is hard to
read; column chunks under ~1,100 px tall are seen at full resolution. Cropping alone
brought Tesseract to 1–3% character errors on alphabetical pages.

**Claude vision models instead of Tesseract.**
Why: on the answer key, Tesseract had 8.9% character errors overall and 32% on the
street guide (leader dots, small number tables), and can't detect bold. Sonnet and
Haiku both read the words almost perfectly (0.0% and 0.2% reading errors).

**Transcribe first, parse second, and keep text as printed.**
Why: a model asked to structure while reading can quietly "fix" or invent fields.
Separate steps keep the raw transcript, so fields can be re-parsed without paying for
OCR again. Spelling, abbreviations and the printer's typos stay; expansions go in new
columns. Every row keeps `raw_text` and its page/column position.

**Haiku for alphabetical and street-guide pages; Sonnet for classified prose pages.**
Why: after parsing, Haiku's tables matched Sonnet's on all but 6 of ~4,500 fields;
checked against the scans, Haiku was right in 4 (Sonnet marked plain listings bold).
Haiku costs ~1/20th as much. On prose, Sonnet copies more faithfully: Haiku changed
the original "Treadwell." to "Treadwell,". Evidence: `scripts/compare_tables.py`,
`data/compare_haiku_vs_sonnet.csv`.

**Rule-based parser for regular pages; Claude extraction for prose pages.**
Why: alphabetical and street pages follow a fixed format, so rules are free, instant,
repeatable and debuggable. Classified paragraphs (officers, dates, capital) are too
free-form for rules; there are few such pages, so ~12¢ each is affordable.

**Route each page by its running header, read by Haiku.**
Why: the header names the page type (e.g. "168 ADA … ADE") for ~0.004¢. The
alphabetical range in it also lets the parser tell a new listing from a wrapped line,
since indentation is lost in transcription. Unreadable headers fall back to content.

**Detect and retry model self-commentary.**
Why: once in 31 calls Sonnet wrote "Wait—bold rules…" into a transcript and repeated
the text. `run_claude.py` now flags such output and retries once at higher effort.

**Accept a Claude-made answer key for the pilot.**
Why: fastest way to compare methods. Known risk: it isn't independent of Sonnet, so
scores may flatter Claude models. A person should check it before relying on scores.

**Defer the Batch API until the full run.**
Why: it halves cost but adds waiting and a second step; savings on 5 pages are
fractions of a cent.
