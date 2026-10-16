"""Transcribe every prepared page with the model its page type calls for.

Routing, per page:
  1. Haiku reads the running header (about 0.004¢).
  2. Alphabetical or Street Guide header -> Haiku transcribes the columns.
     Classified section header (e.g. "Art Societies ... Banks") -> Sonnet.
  3. Header that says neither (an ad cropped instead, a torn page) -> Haiku
     transcribes, then the text decides: leader-dot tables mean Street Guide and
     the page stays on Haiku; anything else is redone with Sonnet and flagged.
  4. Classified pages are then extracted into classified.json by Sonnet.

Work already on disk is reused, so rerunning only pays for what's missing
(--force redoes everything). The decision for each page is saved in
transcripts/routing.json, which scripts/parse_pages.py reads.

Usage:
  .venv/bin/python scripts/pipeline.py --dry-run     # show routes and estimated cost
  .venv/bin/python scripts/pipeline.py               # run
  .venv/bin/python scripts/pipeline.py --pages 0114 0248
"""

import argparse
import json
import shutil
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).resolve().parent))
from extract_classified import extract_page  # noqa: E402
from parse_pages import classify_page, header_page_type, load_page  # noqa: E402
from run_claude import HEADER_SYSTEM, ROOT, load_dotenv, report, transcribe_to_file  # noqa: E402

HAIKU, SONNET = "claude-haiku-5-5", "claude-sonnet-5-5"
MODEL_FOR = {"alphabetical": HAIKU, "street_guide": HAIKU, "classified": SONNET}
EXTRACTOR = SONNET

PREP = ROOT / "data" / "prep"
TRANSCRIPTS = ROOT / "transcripts"
ROUTING = TRANSCRIPTS / "routing.json"

# Measured on the sample pages (USD): per column chunk, per classified page.
COST_PER_CHUNK = {HAIKU: 0.00036, SONNET: 0.0073}
COST_PER_EXTRACTION = 0.12


def chunk_images(page_id):
    return sorted((PREP / page_id).glob("col*_part*.png"))


def missing_chunks(page_id, model, force):
    out = TRANSCRIPTS / model / page_id
    return [(img, out / f"{img.stem}.txt") for img in chunk_images(page_id)
            if force or not (out / f"{img.stem}.txt").exists()]


def run_parallel(client, model, jobs, workers):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda j: transcribe_to_file(client, model, j[0], j[1]), jobs))
    with (TRANSCRIPTS / model / "usage.jsonl").open("a") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
    return results


def read_headers(client, pages, force, workers):
    """Make sure each page has a header.txt read by Haiku; return {page_id: header}."""
    jobs = [(PREP / p / "header.png", TRANSCRIPTS / HAIKU / p / "header.txt") for p in pages
            if force or not (TRANSCRIPTS / HAIKU / p / "header.txt").exists()]
    if jobs:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(
                lambda j: transcribe_to_file(client, HAIKU, j[0], j[1], system=HEADER_SYSTEM), jobs))
        with (TRANSCRIPTS / HAIKU / "usage_headers.jsonl").open("a") as f:
            for r in results:
                f.write(json.dumps(r) + "\n")
        report("headers (Haiku)", results)
    headers = {}
    for p in pages:
        h = TRANSCRIPTS / HAIKU / p / "header.txt"
        headers[p] = h.read_text().strip() if h.exists() else ""
    return headers


def plan(pages, headers, force):
    """Route each page from its header and estimate what running it would cost."""
    routes, cost = {}, 0.0
    for p in pages:
        page_type = header_page_type(headers.get(p, ""))
        model = MODEL_FOR.get(page_type, HAIKU)
        reason = "header" if page_type else "header unclear: Haiku first, then decide by content"
        routes[p] = {"page_type": page_type, "model": model, "reason": reason}
        cost += len(missing_chunks(p, model, force)) * COST_PER_CHUNK[model]
        done = (TRANSCRIPTS / EXTRACTOR / p / "classified.json").exists()
        if page_type == "classified" and (force or not done):
            cost += COST_PER_EXTRACTION
    return routes, cost


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", nargs="*", help="page ids or endings (e.g. 0114); default: all")
    ap.add_argument("--dry-run", action="store_true", help="show routes and cost estimate only")
    ap.add_argument("--force", action="store_true", help="redo work already on disk")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()

    pages = sorted(d.name for d in PREP.iterdir() if (d / "layout.json").exists())
    if args.pages:
        pages = [p for p in pages if any(p.endswith(s) for s in args.pages)]
    if not pages:
        sys.exit("No prepared pages found. Run scripts/prep_pages.py first.")

    if args.dry_run:
        headers = {p: (TRANSCRIPTS / HAIKU / p / "header.txt").read_text().strip()
                   if (TRANSCRIPTS / HAIKU / p / "header.txt").exists() else "" for p in pages}
        routes, cost = plan(pages, headers, args.force)
        for p, r in routes.items():
            todo = len(missing_chunks(p, r["model"], args.force))
            print(f"{p}: {r['page_type'] or 'unknown':<13} -> {r['model']:<18} "
                  f"{todo} chunks to transcribe  ({r['reason']})")
        unread = sum(1 for h in headers.values() if not h)
        print(f"\nEstimated cost: ${cost:.2f}" + (f" (+ headers for {unread} pages, ~$0.00004 each)" if unread else ""))
        return

    load_dotenv()
    client = anthropic.Anthropic()
    headers = read_headers(client, pages, args.force, args.workers)
    routes, _ = plan(pages, headers, args.force)

    # Transcribe each model's pages together so the work runs in parallel.
    for model in (HAIKU, SONNET):
        jobs = [j for p, r in routes.items() if r["model"] == model
                for j in missing_chunks(p, model, args.force)]
        if jobs:
            report(f"columns ({model})", run_parallel(client, model, jobs, args.workers))

    # Pages whose header didn't say: decide from what Haiku read.
    redo = []
    for p, r in routes.items():
        if r["page_type"]:
            continue
        _, lines = load_page(TRANSCRIPTS / HAIKU / p)
        content_type = classify_page(p, headers[p], lines)["page_type"]
        if content_type == "street_guide":
            r.update(page_type="street_guide", reason="header unclear; leader-dot tables in text")
        else:
            r.update(page_type="classified", model=SONNET, needs_review=True,
                     reason="header unclear; text isn't a street guide, so redone with Sonnet "
                            "(could be an alphabetical page with an unreadable header: check)")
            redo += missing_chunks(p, SONNET, args.force)
    if redo:
        report(f"columns ({SONNET}, rerouted)", run_parallel(client, SONNET, redo, args.workers))

    # Sonnet-routed pages need the header next to their columns for parsing.
    for p, r in routes.items():
        src, dst = TRANSCRIPTS / HAIKU / p / "header.txt", TRANSCRIPTS / r["model"] / p / "header.txt"
        if r["model"] != HAIKU and src.exists() and (args.force or not dst.exists()):
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst)

    for p, r in routes.items():
        if r["page_type"] != "classified":
            continue
        if not args.force and (TRANSCRIPTS / EXTRACTOR / p / "classified.json").exists():
            continue
        rec = extract_page(client, EXTRACTOR, TRANSCRIPTS / r["model"] / p)
        status = rec.get("error") or (f"{rec.get('organizations', 0)} organizations"
                                      if rec["stop_reason"] == "end_turn" else rec["stop_reason"])
        print(f"extract {p}: {status}, ${rec.get('cost_usd', 0):.4f}")

    existing = json.loads(ROUTING.read_text()) if ROUTING.exists() else {}
    existing.update(routes)
    ROUTING.write_text(json.dumps(dict(sorted(existing.items())), indent=2) + "\n")
    print(f"\nRouting saved to {ROUTING.relative_to(ROOT)}:")
    for p, r in routes.items():
        flag = "  <- check" if r.get("needs_review") else ""
        print(f"  {p}: {r['page_type']} -> {r['model']}{flag}")


if __name__ == "__main__":
    main()
