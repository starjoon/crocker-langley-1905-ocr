"""Compare two sets of parsed tables field by field (e.g. Haiku vs Sonnet).

Rows are matched by name within each page (listings and streets) or by street and
cross street (crossings), so one missing or extra row doesn't shift the rest.
For every matched pair, each field counts as the same, different only in
capitalization, or different. All differences go to a CSV for review.

Usage:
  .venv/bin/python scripts/compare_tables.py data/tables_haiku data/tables \\
      --out data/compare_haiku_vs_sonnet.csv
"""

import argparse
import csv
import difflib
import re
from collections import defaultdict
from pathlib import Path

TABLES = {
    "directory_entries.csv": {
        "key": lambda r: r["name_raw"],
        "fields": ["entry_type", "bold", "surname", "given_names", "name_title", "name_suffix",
                   "is_widow", "affiliation", "occupation", "employer", "business_address",
                   "residence", "phone", "see_also"],
        "label": lambda r: r["name_raw"],
    },
    "streets.csv": {
        "key": lambda r: r["street_name"],
        "fields": ["qualifier", "former_name", "current_name", "district", "description",
                   "see_reference"],
        "label": lambda r: r["street_name"],
    },
    "street_crossings.csv": {
        "key": lambda r: f"{r['street_name']}|{r['cross_street']}",
        "fields": ["side_a_label", "side_b_label", "side_a_raw", "side_b_raw"],
        "label": lambda r: f"{r['street_name'] or '(previous page)'} x {r['cross_street']}",
    },
}


def norm(v):
    return re.sub(r"\s+", " ", v or "").strip()


def match_key(v):
    return re.sub(r"[^a-z0-9]", "", v.lower())


def load(path):
    return list(csv.DictReader(path.open())) if path.exists() else []


def pair_rows(a_rows, b_rows, key):
    """Align two lists of rows page by page; return (pairs, only_a, only_b)."""
    pairs, only_a, only_b = [], [], []
    pages = sorted({r["page_id"] for r in a_rows + b_rows})
    for page in pages:
        a = [r for r in a_rows if r["page_id"] == page]
        b = [r for r in b_rows if r["page_id"] == page]
        ka, kb = [match_key(key(r)) for r in a], [match_key(key(r)) for r in b]
        sm = difflib.SequenceMatcher(a=ka, b=kb, autojunk=False)
        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal" or (op == "replace" and i2 - i1 == j2 - j1):
                pairs += list(zip(a[i1:i2], b[j1:j2]))  # same position, name may differ
            else:
                only_a += a[i1:i2]
                only_b += b[j1:j2]
    return pairs, only_a, only_b


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("a", type=Path, help="tables folder A (e.g. data/tables_haiku)")
    ap.add_argument("b", type=Path, help="tables folder B (e.g. data/tables)")
    ap.add_argument("--out", type=Path, help="CSV listing every difference")
    args = ap.parse_args()
    name_a, name_b = args.a.name, args.b.name

    diffs = []
    for table, spec in TABLES.items():
        a_rows, b_rows = load(args.a / table), load(args.b / table)
        if not a_rows and not b_rows:
            continue
        pairs, only_a, only_b = pair_rows(a_rows, b_rows, spec["key"])
        print(f"\n== {table}: {len(a_rows)} rows in {name_a}, {len(b_rows)} in {name_b}, "
              f"{len(pairs)} matched, {len(only_a)} only in {name_a}, {len(only_b)} only in {name_b}")
        if spec is TABLES["directory_entries.csv"]:
            renamed = sum(match_key(x["name_raw"]) != match_key(y["name_raw"]) for x, y in pairs)
            print(f"   name_raw differs in {renamed} matched rows")

        counts = defaultdict(lambda: [0, 0, 0])  # same, case only, different
        for ra, rb in pairs:
            for f in spec["fields"]:
                va, vb = norm(ra[f]), norm(rb[f])
                if va == vb:
                    counts[f][0] += 1
                    continue
                kind = "case only" if va.lower() == vb.lower() else "different"
                counts[f][1 if kind == "case only" else 2] += 1
                diffs.append({"table": table, "page_id": rb["page_id"], "row": spec["label"](rb),
                              "field": f, "kind": kind, name_a: va, name_b: vb,
                              "raw_text_" + name_b: rb.get("raw_text", "")})
        print(f"   {'field':<18}{'same':>7}{'case only':>11}{'different':>11}")
        for f in spec["fields"]:
            s, c, d = counts[f]
            print(f"   {f:<18}{s / max(1, len(pairs)):>7.1%}{c:>11}{d:>11}")
        for r in only_a:
            diffs.append({"table": table, "page_id": r["page_id"], "row": spec["label"](r),
                          "field": "(whole row)", "kind": f"only in {name_a}", name_a: spec["label"](r), name_b: ""})
        for r in only_b:
            diffs.append({"table": table, "page_id": r["page_id"], "row": spec["label"](r),
                          "field": "(whole row)", "kind": f"only in {name_b}", name_a: "", name_b: spec["label"](r)})

    if args.out:
        cols = ["table", "page_id", "row", "field", "kind", name_a, name_b, "raw_text_" + name_b]
        with args.out.open("w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(diffs)
        print(f"\n{len(diffs)} differences written to {args.out}")


if __name__ == "__main__":
    main()
