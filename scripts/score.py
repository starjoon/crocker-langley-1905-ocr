"""Score OCR transcriptions against the answer key.

Each method writes one .txt per answer-key chunk into predictions/<method>/,
named like the key file (e.g. predictions/tesseract/p0114_col1_part1.txt) and
following the conventions in answer_key/README.md.

Metrics, per chunk and per method:
  CER    character error rate (edit distance / key length), bold markers removed
  flat   CER after joining all lines and end-of-line hyphens, so it measures
         reading accuracy alone, not where a method broke the lines
  WER    word error rate on the same text
  lines  share of key lines reproduced exactly (in order, via line alignment)
  bold   F1 of bold spans, when the method marks bold at all

Usage: .venv/bin/python scripts/score.py [method ...]   (default: every folder in predictions/)
"""

import difflib
import json
import re
import sys
import unicodedata
from pathlib import Path

from rapidfuzz.distance import Levenshtein

ROOT = Path(__file__).resolve().parent.parent
KEY = ROOT / "answer_key"
PRED = ROOT / "predictions"

BOLD = re.compile(r"\*\*(.+?)\*\*")


def normalize(text):
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("‘", "'").replace("’", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = re.sub(r"[–—―]", "—", text)
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s*(?:\.\s*){2,}", " ... ", line)  # leader dots, any length
        line = re.sub(r"\s+", " ", line).strip()
        if line:
            lines.append(line)
    return lines


def plain(lines):
    return [BOLD.sub(r"\1", l).replace("**", "") for l in lines]


def bold_spans(lines):
    # "**ADAMS WILLIAM,**" and "**ADAMS WILLIAM**," count as the same span.
    return [m.strip().rstrip(",.;:").strip() for l in lines for m in BOLD.findall(l)]


def flatten(lines):
    text = "\n".join(lines)
    text = re.sub(r"-\n", "", text)  # rejoin words hyphenated at a line end
    return re.sub(r"\s+", " ", text).strip()


def score_chunk(key_text, pred_text):
    key_lines, pred_lines = normalize(key_text), normalize(pred_text)
    k, p = plain(key_lines), plain(pred_lines)
    k_str, p_str = "\n".join(k), "\n".join(p)
    k_words, p_words = k_str.split(), p_str.split()

    matcher = difflib.SequenceMatcher(a=k, b=p, autojunk=False)
    exact = sum(b.size for b in matcher.get_matching_blocks())

    result = {
        "cer": Levenshtein.distance(k_str, p_str) / max(1, len(k_str)),
        "flat": Levenshtein.distance(flatten(k), flatten(p)) / max(1, len(flatten(k))),
        "wer": Levenshtein.distance(k_words, p_words) / max(1, len(k_words)),
        "lines": exact / max(1, len(k)),
        "bold": None,
    }
    kb, pb = bold_spans(key_lines), bold_spans(pred_lines)
    if pb:
        hits = sum(min(kb.count(s), pb.count(s)) for s in set(kb))
        prec, rec = hits / len(pb), hits / max(1, len(kb))
        result["bold"] = 2 * prec * rec / (prec + rec) if hits else 0.0
    return result


def fmt(v):
    return "   -  " if v is None else f"{v:6.1%}"


def main():
    manifest = json.loads((KEY / "manifest.json").read_text())
    methods = sys.argv[1:] or sorted(d.name for d in PRED.iterdir() if d.is_dir()) if PRED.exists() else []
    if not methods:
        sys.exit("No predictions found. Put each method's output in predictions/<method>/.")

    summary = []
    for method in methods:
        print(f"\n== {method} ==")
        print(f"{'chunk':<20} {'type':<17} {'CER':>6} {'flat':>6} {'WER':>6} {'lines':>6} {'bold':>6}")
        rows = []
        for chunk, info in manifest.items():
            pred_file = PRED / method / f"{chunk}.txt"
            if not pred_file.exists():
                print(f"{chunk:<20} missing")
                continue
            r = score_chunk((KEY / f"{chunk}.txt").read_text(), pred_file.read_text())
            rows.append(r)
            print(f"{chunk:<20} {info['page_type']:<17} {fmt(r['cer'])} {fmt(r['flat'])} {fmt(r['wer'])} {fmt(r['lines'])} {fmt(r['bold'])}")
        if rows:
            avg = {m: sum(r[m] for r in rows) / len(rows) for m in ("cer", "flat", "wer", "lines")}
            bolds = [r["bold"] for r in rows if r["bold"] is not None]
            avg["bold"] = sum(bolds) / len(bolds) if bolds else None
            print(f"{'mean':<38} {fmt(avg['cer'])} {fmt(avg['flat'])} {fmt(avg['wer'])} {fmt(avg['lines'])} {fmt(avg['bold'])}")
            summary.append((method, avg, len(rows)))

    if len(summary) > 1:
        print("\n== ranking by CER ==")
        for method, avg, n in sorted(summary, key=lambda s: s[1]["cer"]):
            print(f"{method:<24} CER {fmt(avg['cer'])}  flat {fmt(avg['flat'])}  WER {fmt(avg['wer'])}  lines {fmt(avg['lines'])}  ({n} chunks)")


if __name__ == "__main__":
    main()
