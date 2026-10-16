# Answer key

Hand transcriptions of one chunk from each sample page, used to score OCR methods
with `scripts/score.py`. Each file is the correct text for the image named in
`manifest.json`, which also lists each chunk's page type.

**Status:** draft transcribed by Claude from the crops. Check each file against
its image before relying on the scores, especially names, house numbers, and
punctuation.

## Conventions

A transcription method's output must follow these rules for its score to be fair.

- **One printed line per line**, top to bottom, exactly as broken on the page.
  Continuation lines are separate lines, and an end-of-line hyphen stays
  (`West-` / `ern Assurance`). Joining entries happens later, in parsing.
- **Indentation is not recorded.**
- **Spelling and abbreviations as printed**: `clk`, `r`, `bkkpr`, `Devisadero`,
  `N Ws`. No expanding or correcting.
- **Bold** is wrapped in `**…**`, one span per line (a bold name that wraps is
  closed at the line end and reopened on the next line).
- **Small caps** are written in normal mixed case (`Art Association, The San
  Francisco`). Italics are not marked.
- **Leader dots** in the Street Guide are written as ` ... ` whatever their length.
  A blank number cell printed as a dash is `—`.
- **Symbols**: pointing hand `☞`, fractions `½`, em dash `—`.
- **Damaged type** is written as the letter it clearly is (`BO^RD` → `BOARD`).
- **Running headers, page numbers, and ads are excluded.**
