"""Extract organizations from classified (prose) pages into structured JSON.

Classified pages (Banks, Art Societies, ...) are free-form paragraphs naming
officers, dates and capital, so Claude reads each page's transcript and fills a
fixed JSON schema. The result goes to transcripts/<model>/<page_id>/classified.json,
which scripts/parse_pages.py turns into organizations.csv and officers.csv.

Usage: .venv/bin/python scripts/extract_classified.py [--model claude-sonnet-5-5]
"""

import argparse
import json
import sys
from pathlib import Path

import anthropic

sys.path.insert(0, str(Path(__file__).resolve().parent))
from parse_pages import ROOT, classify_page, load_page  # noqa: E402
from run_claude import PRICES, load_dotenv  # noqa: E402

SYSTEM = """You extract structured data from transcribed pages of the 1905 \
Crocker-Langley San Francisco Directory. The text comes from the directory's \
classified sections (banks, societies, institutions), where each organization is \
one paragraph of prose. Line breaks in the text are the printed line breaks, \
and a word broken at a line end ends with "-".

Return every organization entry on the page, in order, including short entries \
and cross references ("Bank of California. See The Bank of California."). Rules:

- Copy names, places, dates, and amounts exactly as printed. Do not modernize \
spelling or expand abbreviations. Rejoin words broken across lines.
- section: the section heading the entry falls under (e.g. "Banks"). For entries \
before the first heading on the page, use the section named in the page header \
given below.
- people: every person named with a position, one item per person, with role in \
singular form as printed ("President", "Vice-President", "Director", "London \
Director", "Assistant Cashier"). Directors listed together each get role "Director". \
If the role is at a different organization than the entry ("Homer S. King, \
President Bank of California" on a committee), put that organization's name in \
role_organization; otherwise role_organization is "".
- address, organized, incorporated, financials, see_reference: "" when absent. \
financials is the capital, surplus, shares, etc. text, copied as printed.
- raw_text: the entry's full text as printed, lines joined with single spaces.
- continued_from_previous_page: true only for a first entry whose beginning is \
missing (the page opens mid-paragraph). continues_on_next_page: true only for a \
last entry that is cut off at the end of the page."""

PERSON = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "role": {"type": "string"},
                   "role_organization": {"type": "string"}},
    "required": ["name", "role", "role_organization"],
    "additionalProperties": False,
}
ORG_FIELDS = ["section", "name", "address", "organized", "incorporated", "financials",
              "see_reference", "raw_text"]
SCHEMA = {
    "type": "object",
    "properties": {
        "organizations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    **{f: {"type": "string"} for f in ORG_FIELDS},
                    "people": {"type": "array", "items": PERSON},
                    "continued_from_previous_page": {"type": "boolean"},
                    "continues_on_next_page": {"type": "boolean"},
                },
                "required": ORG_FIELDS + ["people", "continued_from_previous_page",
                                          "continues_on_next_page"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["organizations"],
    "additionalProperties": False,
}


def page_text(lines):
    out, last_col = [], None
    for col, text in lines:
        if last_col is not None and col != last_col:
            out.append(f"\n[column {col}]")
        out.append(text)
        last_col = col
    return "\n".join(out)


def extract_page(client, model, page_dir):
    """Extract one classified page into page_dir/classified.json; return a usage record.

    The record is also appended to transcripts/<model>/usage_classified.jsonl.
    """
    header, lines = load_page(page_dir)
    prompt = f"Page header: {header}\n\nPage text:\n\n{page_text(lines)}"
    kwargs = dict(
        model=model,
        max_tokens=32000,
        system=SYSTEM,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": prompt}],
    )
    try:
        if model == "claude-sonnet-5-5":
            with client.beta.messages.stream(
                betas=["server-side-fallback-2026-07-01"], fallbacks="default", **kwargs
            ) as stream:
                msg = stream.get_final_message()
        else:
            with client.messages.stream(**kwargs) as stream:
                msg = stream.get_final_message()
    except (anthropic.AuthenticationError, TypeError):
        sys.exit("No valid Anthropic API key. Set ANTHROPIC_API_KEY and try again.")
    except anthropic.APIError as e:
        return {"page_id": page_dir.name, "model": model, "error": str(e)}

    price_in, price_out = PRICES[model]
    u = msg.usage
    record = {
        "page_id": page_dir.name, "model": model, "stop_reason": msg.stop_reason,
        "input_tokens": u.input_tokens, "output_tokens": u.output_tokens,
        "cost_usd": (u.input_tokens * price_in + u.output_tokens * price_out) / 1e6,
    }
    if msg.stop_reason == "end_turn":
        data = json.loads(next(b.text for b in msg.content if b.type == "text"))
        (page_dir / "classified.json").write_text(json.dumps(data, indent=2, ensure_ascii=False))
        record["organizations"] = len(data["organizations"])
    with (ROOT / "transcripts" / model / "usage_classified.jsonl").open("a") as f:
        f.write(json.dumps(record) + "\n")
    return record


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="claude-sonnet-5-5", choices=sorted(PRICES))
    args = ap.parse_args()
    load_dotenv()
    client = anthropic.Anthropic()

    for page_dir in sorted((ROOT / "transcripts" / args.model).iterdir()):
        if not page_dir.is_dir():
            continue
        header, lines = load_page(page_dir)
        if classify_page(page_dir.name, header, lines)["page_type"] != "classified":
            continue
        r = extract_page(client, args.model, page_dir)
        if "error" in r or r["stop_reason"] != "end_turn":
            print(f"{page_dir.name}: not saved ({r.get('error') or r['stop_reason']})")
        else:
            print(f"{page_dir.name}: {r['organizations']} organizations, "
                  f"{r['input_tokens']:,} in + {r['output_tokens']:,} out tokens, ${r['cost_usd']:.4f}")


if __name__ == "__main__":
    main()
