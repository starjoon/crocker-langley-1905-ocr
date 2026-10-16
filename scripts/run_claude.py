"""Transcribe column chunks with a Claude model and log what it cost.

  --set key   the answer-key chunks -> predictions/<model>/<chunk>.txt (score with score.py)
  --set all   every chunk from prep_pages.py -> transcripts/<model>/<page_id>/<chunk>.txt
  --set headers  each page's running header -> transcripts/<model>/<page_id>/header.txt

Token counts and cost per chunk are appended to usage.jsonl next to the output.
For whole pages, scripts/pipeline.py picks the model per page and calls this.

Needs Anthropic API credentials: ANTHROPIC_API_KEY in the environment, or a line
ANTHROPIC_API_KEY=... in a .env file at the project root (git-ignored).

Usage:
  .venv/bin/python scripts/run_claude.py --model claude-haiku-5-5
  .venv/bin/python scripts/run_claude.py --model claude-sonnet-5-5 --set all
"""

import argparse
import base64
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic

ROOT = Path(__file__).resolve().parent.parent

# USD per million tokens (input, output), Claude API standard rates.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-opus-5-5": (4.00, 20.00),
}

SYSTEM = """You transcribe scanned pages of the 1905 Crocker-Langley San Francisco \
Directory. You are given one column (or part of one) at a time. Copy the printed \
text exactly, following these rules:

- One printed line per output line, top to bottom, broken exactly where the page \
breaks it. A continuation line is its own line; keep an end-of-line hyphen as printed.
- Do not record indentation.
- Keep spelling and abbreviations exactly as printed (clk, r, bkkpr, mfrs agt, \
Devisadero, N Ws). Never expand, correct, or modernize anything.
- Wrap bold text in **...**, one span per line: if a bold name wraps onto the next \
line, close ** at the end of the line and reopen it on the next.
- Write small capitals in normal mixed case. Do not mark italics.
- In tables with leader dots (Street Guide), write the dots as " ... " whatever \
their length. A dash in a number column is written as —.
- Symbols: pointing hand ☞, fractions such as ½, em dash —.
- Worn or broken type: write the letter it clearly is. If characters are truly \
unreadable, write [?] in their place.
- Skip running headers (section title, alphabetical range, page number), \
advertisements, and partial lines cut off at the very top or bottom edge only if \
no letters are legible.

Output only the transcription: no preamble, notes, or code fences. Your output \
is saved verbatim as data, so never comment on or correct your own work in it; \
settle any doubts before you write, and use [?] for anything you cannot read."""

# A model sometimes "thinks out loud" in its answer ("Wait—the first line is bold")
# and then writes the text again. Such output is retried at higher effort.
COMMENTARY = re.compile(
    r"^\s*(wait\b|actually\b|hmm\b|let me\b|note:|i (think|see|need|will|should)\b|"
    r"looking (again|closer)|correction\b|on closer)",
    re.IGNORECASE | re.MULTILINE,
)
HEADER_SYSTEM = """You transcribe the running header line of a scanned page from the \
1905 Crocker-Langley San Francisco Directory, e.g. "168 ADA CROCKER-LANGLEY \
DIRECTORY. ADE". Copy it exactly as printed on one line, left to right, with single \
spaces between parts. Output only that line."""

NEXT_EFFORT = {"low": "medium", "medium": "high", "high": "high"}


def load_dotenv():
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        key, sep, value = line.strip().partition("=")
        if sep and not key.startswith("#"):
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def chunks_for(which):
    if which == "key":
        manifest = json.loads((ROOT / "answer_key" / "manifest.json").read_text())
        out = ROOT / "predictions"
        return [(ROOT / info["image"], Path(f"{name}.txt")) for name, info in manifest.items()], out
    out = ROOT / "transcripts"
    pattern = "*/header.png" if which == "headers" else "*/col*_part*.png"
    images = sorted((ROOT / "data" / "prep").glob(pattern))
    return [(p, Path(p.parent.name) / f"{p.stem}.txt") for p in images], out


def transcribe(client, model, effort, image, system=SYSTEM):
    data = base64.standard_b64encode(image.read_bytes()).decode("utf-8")
    kwargs = dict(
        model=model,
        max_tokens=16000,
        system=system,
        output_config={"effort": effort},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}},
                {"type": "text", "text": "Transcribe this image."},
            ],
        }],
    )
    if model == "claude-sonnet-5-5":
        # If a safety classifier wrongly declines a page, the API reruns it on
        # another model inside the same call instead of returning nothing.
        return client.beta.messages.create(
            betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
        )
    return client.messages.create(**kwargs)


def transcribe_to_file(client, model, image, dest, effort="low", system=SYSTEM):
    """Transcribe one image into dest; return a usage record for the cost log.

    If the answer contains the model's own commentary, retry once at higher effort.
    """
    price_in, price_out = PRICES[model]
    tin = tout = attempts = 0
    while True:
        attempts += 1
        try:
            msg = transcribe(client, model, effort, image, system)
        except (anthropic.AuthenticationError, TypeError):
            # TypeError: the SDK found no credentials at all.
            sys.exit("No valid Anthropic API key. Set ANTHROPIC_API_KEY and try again.")
        except anthropic.APIError as e:
            return {"chunk": str(dest), "model": model, "error": str(e)}
        tin += msg.usage.input_tokens
        tout += msg.usage.output_tokens
        text = "".join(b.text for b in msg.content if b.type == "text")
        flagged = bool(COMMENTARY.search(text))
        if not flagged or attempts == 2:
            break
        effort = NEXT_EFFORT[effort]
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text.strip() + "\n")
    return {
        "chunk": str(dest.relative_to(ROOT)) if dest.is_relative_to(ROOT) else str(dest),
        "model": model,
        "stop_reason": msg.stop_reason,
        "effort": effort,
        "attempts": attempts,
        "flagged": flagged,
        "input_tokens": tin,
        "output_tokens": tout,
        "cost_usd": (tin * price_in + tout * price_out) / 1e6,
    }


def report(label, results):
    """Print problems and a cost summary for a batch of transcribe_to_file results."""
    ok = [r for r in results if "error" not in r]
    for r in results:
        if "error" in r:
            print(f"{r['chunk']}: ERROR {r['error']}")
        elif r["stop_reason"] != "end_turn":
            print(f"{r['chunk']}: stopped early ({r['stop_reason']})")
        elif r["flagged"]:
            print(f"{r['chunk']}: still contains commentary after a retry - review by hand")
        elif r["attempts"] > 1:
            print(f"{r['chunk']}: commentary in first answer, clean on retry at {r['effort']} effort")
    tin = sum(r["input_tokens"] for r in ok)
    tout = sum(r["output_tokens"] for r in ok)
    cost = sum(r["cost_usd"] for r in ok)
    print(f"{label}: {len(ok)}/{len(results)} chunks, {tin:,} input + {tout:,} output tokens, ${cost:.4f}")
    return cost


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, choices=sorted(PRICES))
    ap.add_argument("--set", default="key", choices=["key", "all", "headers"])
    ap.add_argument("--effort", default="low", choices=["low", "medium", "high"])
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--only", help="rerun only chunks whose path contains this text")
    args = ap.parse_args()

    jobs, out_root = chunks_for(args.set)
    if args.only:
        jobs = [j for j in jobs if args.only in str(j[1])]
    out_dir = out_root / args.model
    out_dir.mkdir(parents=True, exist_ok=True)
    load_dotenv()
    client = anthropic.Anthropic()
    system = HEADER_SYSTEM if args.set == "headers" else SYSTEM

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(
            lambda job: transcribe_to_file(client, args.model, job[0], out_dir / job[1],
                                           args.effort, system),
            jobs,
        ))

    usage_file = "usage_headers.jsonl" if args.set == "headers" else "usage.jsonl"
    with (out_dir / usage_file).open("a") as f:  # append: the log is the spend history
        for r in results:
            f.write(json.dumps(r) + "\n")
    report(args.model, results)


if __name__ == "__main__":
    main()
