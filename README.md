# Crocker-Langley 1905 directory OCR

Turns scanned pages of the 1905 Crocker-Langley San Francisco Directory into text,
then (later) into structured tables for modeling.

## Pipeline

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
# Put ANTHROPIC_API_KEY=... in .env

# 1. Crop ads, split columns, cut columns into chunks -> data/prep/<page>/
.venv/bin/python scripts/prep_pages.py

# 2. Transcribe, routing each page to a model by its type (see below)
.venv/bin/python scripts/pipeline.py --dry-run   # routes + cost estimate, no API calls
.venv/bin/python scripts/pipeline.py

# 3. Build tables (formats in docs/schema.md) -> data/tables/*.csv
.venv/bin/python scripts/parse_pages.py
.venv/bin/python -m pytest tests/
```

**Model routing** (`scripts/pipeline.py`): Haiku reads each page's running header;
alphabetical and Street Guide pages are transcribed by Haiku (~0.2¢ per page),
classified prose pages by Sonnet (~4¢) and then extracted by Sonnet (~12¢). A page
whose header can't be read is transcribed by Haiku and classified from its text;
if it isn't a Street Guide it is redone with Sonnet and marked `needs_review`.
Decisions are saved in `transcripts/routing.json`, and `pages.csv` records which
model transcribed each page. Work already on disk is never paid for twice.

Every API call is appended to a cost log: `transcripts/<model>/usage*.jsonl`.

**Comparing methods** (used to choose the models):

```bash
.venv/bin/python scripts/run_claude.py --model claude-haiku-5-5        # answer-key chunks
.venv/bin/python scripts/run_tesseract.py                              # free baseline
.venv/bin/python scripts/score.py                                      # score vs answer key
.venv/bin/python scripts/parse_pages.py --model claude-sonnet-5-5 --out data/tables_sonnet
.venv/bin/python scripts/compare_tables.py data/tables_haiku data/tables_sonnet
```

| Folder | Contents |
|---|---|
| `data/raw/` | original `.jp2` scans |
| `data/prep/` | generated crops and `debug.png` overlays (not tracked) |
| `answer_key/` | hand transcriptions and the transcription rules |
| `predictions/<method>/` | each method's output on the answer-key chunks |
| `transcripts/<model>/<page>/` | full-page transcriptions, with `usage.jsonl` cost logs |
| `data/tables/` | the structured tables: pages, directory entries, streets, crossings, organizations, officers |
| `data/tables_haiku/`, `data/tables_sonnet/` | the same tables built from one model only, for comparison |
| `transcripts/routing.json` | which model each page was routed to, and why |
| `docs/schema.md` | what every table and column means |

## Results so far (5 sample pages, 2026-10-16)

Scores on the 5 answer-key chunks (~210 lines). "Reading errors" ignores where
lines were broken.

| Method | Character errors | Reading errors | Lines exactly right | Cost per chunk |
|---|---|---|---|---|
| Claude Sonnet 5.5 | 0.0% | 0.0% | 100% | ~0.7¢ |
| Claude Haiku 5.5 | 1.9% | 0.2% | 28% | ~0.04¢ |
| Tesseract | 8.9% | 8.9% | 72% | free |

- **Sonnet** copies the page faithfully, including the 1905 printer's own typos.
  In one of 31 calls it wrote a note to itself into the output; the runner now
  detects this and retries.
- **Haiku** reads almost as well but doesn't follow the format: it often joins
  wrapped lines, writes small capitals in full caps, misses bold, and
  "corrects" punctuation (`Treadwell.` → `Treadwell,`).
- **Tesseract** is usable on alphabetical pages (1–3%) but fails on the Street
  Guide tables (32%).
- **Caveat:** the answer key was transcribed by Claude, so Sonnet's perfect score
  may partly reflect shared misreadings. A person should check the key.

Measured cost: about 0.7¢ per chunk with Sonnet and 6 chunks per page, so
~4.4¢ per page, or ~$90 for 2,000 pages (~$45 with the Batch API).

### Haiku vs Sonnet after parsing (2026-10-16)

Haiku's transcripts of the 4 rule-parsed pages, run through the same parser
(`data/tables_haiku/`), match Sonnet's tables in all but 6 of ~4,500 fields
(`scripts/compare_tables.py`, details in `data/compare_haiku_vs_sonnet.csv`).
Checked against the scans, Haiku was right in 4 (Sonnet marked 3 plain listings
bold and wrote `glassWkr`) and Sonnet in 2 (Haiku dropped a comma and a dash).
So Haiku (~0.2¢ per page) is the choice for alphabetical and street-guide pages;
classified pages stay on Sonnet.

## Known issues

- On p.75 the "STREET AND AVENUE GUIDE." header ends up at the top of columns 2–3
  (the models skip it, Tesseract doesn't).
- Bold detection is unreliable for small-capital headings on the classified pages.
- Pages aren't straightened before cropping.
