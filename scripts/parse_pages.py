"""Turn Sonnet transcripts into the CSV tables described in docs/schema.md.

Reads each page's transcript (header.txt plus colN_partM.txt chunks) and writes
data/tables/*.csv. By default each page is read from the model that
scripts/pipeline.py routed it to (transcripts/routing.json); --model reads every
page from one model's folder instead, e.g. to compare models. Alphabetical and Street Guide pages are parsed by the
rules below; classified (prose) pages are read from the JSON that
scripts/extract_classified.py writes.

Usage: .venv/bin/python scripts/parse_pages.py [--model routed|claude-haiku-5-5|...] [--out data/tables]
"""

import argparse
import csv
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TABLES = ROOT / "data" / "tables"

# ---------------------------------------------------------------- page loading

ALPHA_HEADER = re.compile(r"^(\d+)?\s*([A-Z]{2,4})\s+CROCKER-LANGLEY DIRECTORY\.?\s+([A-Z]{2,4})\s*(\d+)?$")
SECTION_HEADER = re.compile(r"^(\d+)?\s*(.*?)\s*CROCKER-LANGLEY DIRECTORY\.?\s*(.*?)\s*(\d+)?$")


def chunk_key(path):
    m = re.match(r"col(\d+)_part(\d+)", path.stem)
    return int(m[1]), int(m[2])


def load_page(page_dir):
    """Return the header text and the page's lines in reading order as (column, text)."""
    header_file = page_dir / "header.txt"
    header = header_file.read_text().strip() if header_file.exists() else ""
    lines = []
    for chunk in sorted(page_dir.glob("col*_part*.txt"), key=chunk_key):
        col = chunk_key(chunk)[0]
        lines += [(col, normalize_line(l)) for l in chunk.read_text().splitlines() if l.strip()]
    return header, lines


def normalize_line(line):
    # Leader dots may be transcribed at any length ("Geary ..........1300").
    line = re.sub(r"\s*\.{2,}\s*", " ... ", line).strip()
    # Italics aren't used; drop single-asterisk marks ("*Cross Sts.*"), keep **bold**.
    line = re.sub(r"(?<!\*)\*(?!\*)", "", line)
    return re.sub(r"\s+", " ", line)


def header_page_type(header):
    """Page type from the running header alone, or None if the header doesn't say."""
    if ALPHA_HEADER.match(header):
        return "alphabetical"
    if "STREET AND AVENUE GUIDE" in header:
        return "street_guide"
    if "CROCKER-LANGLEY DIRECTORY" in header:
        return "classified"
    return None


def classify_page(page_id, header, lines):
    info = {"page_id": page_id, "printed_page": "", "page_type": "", "header_raw": header,
            "range_start": "", "range_end": "", "section_left": "", "section_right": ""}
    m = ALPHA_HEADER.match(header)
    if m:
        info.update(printed_page=m[1] or m[4] or "", page_type="alphabetical",
                    range_start=m[2], range_end=m[3])
        return info
    leader_share = sum(" ... " in t or t.endswith("...") for _, t in lines) / max(1, len(lines))
    if "STREET AND AVENUE GUIDE" in header or leader_share > 0.2:
        info["page_type"] = "street_guide"
        n = re.search(r"\b(\d+)\b", header) if "DIRECTORY" in header or "GUIDE" in header else None
        info["printed_page"] = n[1] if n else ""
        return info
    m = SECTION_HEADER.match(header)
    info["page_type"] = "classified"
    if m:
        info.update(printed_page=m[1] or m[4] or "",
                    section_left=m[2].rstrip(". "), section_right=m[3].rstrip(". "))
    return info


# Words that keep their hyphen when split at a line end: "Twenty-" + "fourth".
KEEP_HYPHEN = re.compile(
    r"(?i)\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|vice|ex|self|at)-$"
)


def join_lines(lines):
    """Join printed lines into one string, rejoining words hyphenated at line ends.

    A line-end hyphen is usually a word break ("West-" + "ern"), but can be a real
    hyphen ("Twenty-" + "fourth", "137-139-" + "141"); KEEP_HYPHEN and digits on
    both sides cover the cases seen so far.
    """
    out = ""
    for line in lines:
        if not out:
            out = line
        elif out.endswith("-") and KEEP_HYPHEN.search(out):
            out += line
        elif out.endswith("-") and not (out[-2:-1].isdigit() and line[:1].isdigit()):
            out = out[:-1] + line  # "West-" + "ern" -> "Western"
        elif out.endswith("-"):
            out += line  # "137-139-" + "141" keeps the hyphen
        else:
            out += " " + line
    return out


def strip_bold(text):
    return text.replace("**", "")


def short_id(page_id):
    return page_id.rsplit("_", 1)[-1]


# ---------------------------------------------------------- alphabetical pages

ORG_WORDS = {
    "&", "co", "company", "board", "assn", "association", "society", "soc", "church",
    "school", "academy", "college", "hall", "library", "house", "building", "bldg",
    "warehouses", "warehouse", "foundry", "union", "convent", "kindergarten", "bros",
    "the", "inc", "corporation", "wks", "works", "agency", "steamers", "club", "hotel",
    "bank", "mfg", "fund", "store", "lodge", "of", "and",
}
TITLES = {"mrs", "miss", "mr", "dr", "rev", "capt", "prof", "bro", "sister"}
SUFFIXES = {"jr", "sr"}
ADDRESS_WORDS = re.compile(
    r"\b(Bldg|Building|Hall|cor|floor|Wharf|Pier|Exchange|ft|room)\b"
)
ORDINAL_STREET = re.compile(r"^\d+(st|d|nd|rd|th)\b")
HOUSE_NUMBER = re.compile(r"^(r\s+)?\d+[a-z½]*$")
CROSS_REF = re.compile(r"^☞?\s*(.+?)\.\s+See\s+(.+?)\.?$")


def first_letters(text):
    word = strip_bold(text).lstrip("☞ ").split(" ")[0]
    return re.sub(r"[^A-Za-z]", "", word).upper()


def starts_entry(line, prev, rng):
    """Is this printed line the start of a new listing?

    Indentation is lost in transcription, so a listing starts where a line opens
    with a name inside the page's alphabetical range (ADA-ADE): a wrapped line
    like "Howard" or "Co, r 954 Ashbury" falls outside it.
    """
    text = strip_bold(line)
    if text.startswith("☞"):
        return True
    if prev is not None and strip_bold(prev).endswith("-"):
        return False
    if not text[:1].isupper():
        return False
    key = first_letters(line)
    start, end = rng
    return start <= key[: len(start)] and key[: len(end)] <= end


def split_segments(text):
    """Split on commas outside parentheses, then rejoin '1465, 11th Av' style addresses."""
    parts, depth, cur = [], 0, ""
    for ch in text:
        depth += (ch == "(") - (ch == ")")
        if ch == "," and depth == 0:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    parts.append(cur.strip())
    parts = [p for p in parts if p]
    merged = []
    for p in parts:
        if merged and HOUSE_NUMBER.match(merged[-1]) and ORDINAL_STREET.match(p):
            merged[-1] += ", " + p
        else:
            merged.append(p)
    return merged


def is_caps(text):
    letters = re.sub(r"[^A-Za-z]", "", text)
    return len(letters) > 1 and letters.isupper()


def split_name(text):
    """Return (name, affiliation, rest) for a listing's text."""
    # Bold all-caps names can contain commas: "SAGE, DEALEY & CO (O G Sage ...)".
    end = len(text)
    for sep in (",", " ("):
        i = text.find(sep)
        if i != -1:
            end = min(end, i)
    name = text[:end]
    rest = text[end:]
    while is_caps(name) and rest.startswith(","):
        nxt = re.match(r",\s*([^,(]+)", rest)
        if not nxt or not is_caps(nxt[1]):
            break
        name += "," + nxt[0][1:].rstrip()
        rest = rest[nxt.end():]
    rest = rest.lstrip(", ")
    affiliation = ""
    if rest.startswith("("):
        depth = 0
        for i, ch in enumerate(rest):
            depth += (ch == "(") - (ch == ")")
            if depth == 0:
                affiliation, rest = rest[1:i], rest[i + 1:].lstrip(", ")
                break
    return name.strip().rstrip("."), affiliation.strip(), rest.strip()


def is_organization(name):
    words = [w.lower().strip(".,'") for w in name.split()]
    return any(w in ORG_WORDS for w in words) or len(words) > 5


def parse_person_name(name):
    tokens = name.replace(",", " ").split()
    title, suffix, kept = [], [], []
    for t in tokens:
        low = t.lower().strip(".")
        if low in TITLES:
            title.append(t)
        elif low in SUFFIXES:
            suffix.append(t)
        else:
            kept.append(t)
    norm = [t.title() if t.isupper() and len(t) > 1 else t for t in kept]
    return {
        "surname": norm[0] if norm else "",
        "given_names": " ".join(norm[1:]),
        "name_title": " ".join(title),
        "name_suffix": " ".join(suffix),
    }


def split_role(role):
    """'bkkpr Broadway Brewery' -> ('bkkpr', 'Broadway Brewery')."""
    words = role.split()
    i = 0
    while i < len(words) and (words[i][:1].islower() or words[i] == "&"):
        i += 1
    occupation, employer = " ".join(words[:i]), " ".join(words[i:])
    if occupation == "with":  # "with W J Adams": works for, no trade given
        occupation = ""
    return occupation, employer


def parse_listing(text):
    row = {k: "" for k in (
        "entry_type", "name_raw", "surname", "given_names", "name_title", "name_suffix",
        "affiliation", "role_raw", "occupation", "employer", "business_address",
        "residence", "phone", "see_also")}
    row["is_widow"] = False
    warnings = []

    m = CROSS_REF.match(text)
    if m and len(m[1].split()) <= 6:
        row.update(entry_type="cross_reference", name_raw=m[1].strip(), see_also=m[2].strip())
        return row, warnings

    name, row["affiliation"], rest = split_name(text)
    row["name_raw"] = name
    org = is_organization(name)
    row["entry_type"] = "organization" if org else "person"
    if not org:
        row.update(parse_person_name(name))

    roles, business = [], []
    for seg in split_segments(rest):
        if row["residence"]:
            row["residence"] += ", " + seg  # rare: text after the residence
            warnings.append("residence has several parts")
            continue
        mid = re.match(r"^(.*?)\s+r\s+(\d.*)$", seg)  # "... Co r 1704 Waller"
        if seg.startswith("r ") or seg == "r":
            row["residence"] = seg[2:].strip()
        elif mid and not seg.startswith("tel "):
            roles.append(mid[1]) if not roles else business.append(mid[1])
            row["residence"] = mid[2]
        elif seg.startswith("tel "):
            row["phone"] = seg[4:].strip()
        elif re.match(r"^(office\b|\d)", seg) or ADDRESS_WORDS.search(seg):
            business.append(seg)
        elif not roles:
            roles.append(seg)
        elif org:
            roles.append(seg)
        elif seg[:1].islower():
            roles[0] += ", " + seg  # "widow, teacher elocution"

        elif not business and not row["employer"]:
            row["employer"] = seg  # "mechanic, Cal Elec Wks"
        else:
            business.append(seg)

    tel = re.search(r",?\s*\btel\s+(.+)$", ", ".join(business))
    if tel and not row["phone"]:
        row["phone"] = tel[1]
        business = [re.sub(r"\btel\s+.+$", "", ", ".join(business)).strip(", ")]
    row["role_raw"] = ", ".join(roles)
    row["business_address"] = ", ".join(b for b in business if b)

    if not org and roles:
        occupation, employer = split_role(roles[0])
        row["occupation"] = occupation
        row["employer"] = ", ".join(e for e in (employer, row["employer"]) if e)
        if not occupation and employer:
            warnings.append("role starts with a capital: occupation unclear")
    row["is_widow"] = row["occupation"].startswith("widow")
    if not org and not row["surname"]:
        warnings.append("no name found")
    return row, warnings


def parse_alphabetical(info, lines):
    rng = (info["range_start"], info["range_end"])
    groups, prev = [], None
    for col, line in lines:
        if not groups or starts_entry(line, prev, rng):
            groups.append({"col": col, "lines": [], "continued": not groups and not starts_entry(line, prev, rng)})
        groups[-1]["lines"].append(line)
        prev = line

    rows, seq = [], {}
    for g in groups:
        seq[g["col"]] = seq.get(g["col"], 0) + 1
        text = join_lines([strip_bold(l) for l in g["lines"]])
        row, warnings = parse_listing(text)
        rows.append({
            "entry_id": f"{short_id(info['page_id'])}-c{g['col']}-{seq[g['col']]:03d}",
            "page_id": info["page_id"],
            "printed_page": info["printed_page"],
            "column": g["col"],
            "seq": seq[g["col"]],
            "bold": g["lines"][0].startswith("**"),
            **row,
            "continued": g["continued"],
            "warnings": "; ".join(warnings),
            "raw_text": " / ".join(strip_bold(l) for l in g["lines"]),
        })
    return rows


# ---------------------------------------------------------- street guide pages

VALUE = r"(\d+|\([a-z]\)|—)"
ROW_TAIL = re.compile(rf"^(?P<name>.+?)[\s.]*\s(?P<a>{VALUE})(?:\s+(?P<b>{VALUE}))?$")
BOLD_START = re.compile(r"^\*\*(.+?)\*\*,?\s*(.*)$")


def parse_row(line):
    """'Stockton ... 200 201' -> ('Stockton', '200', '201')."""
    if "..." in line:
        name, _, tail = line.partition("...")
        values = [v for v in re.split(r"[\s.]+", tail) if v]
        values += ["", ""]
        return name.strip(" ."), values[0], values[1]
    m = ROW_TAIL.match(line)
    if m:
        return m["name"].strip(" ."), m["a"], m["b"] or ""
    return None


def describe_street(text):
    out = {"qualifier": "", "former_name": "", "current_name": "", "district": "",
           "description": "", "see_reference": ""}
    text = text.strip(", ")
    if text.startswith("("):
        close = text.find(")")
        out["qualifier"], text = text[1:close], text[close + 1:].strip(", ")
        q = out["qualifier"]
        if q.startswith("late "):
            out["former_name"] = q[5:]
        elif q.startswith("now "):
            out["current_name"] = q[4:]
        else:
            out["district"] = q
    see = re.search(r"\bsee\s+(.+)$", text)
    if see:
        out["see_reference"] = see[1].strip(". ")
        text = text[: see.start()].strip(", ")
    out["description"] = text
    return out


def parse_street_guide(info, lines):
    streets, crossings = [], []
    current, labels, seq, desc_lines = None, ("", ""), {}, []
    pid = short_id(info["page_id"])

    def close_street():
        if current is not None:
            text = join_lines([strip_bold(l) for l in desc_lines])
            current.update(describe_street(text))
            current["raw_text"] = " / ".join([current.pop("_first")] + desc_lines)

    in_table = True  # rows at the top of a page continue the previous page's table
    for col, line in lines:
        bold = BOLD_START.match(line)
        if bold:
            close_street()
            seq[col] = seq.get(col, 0) + 1
            current = {
                "street_id": f"{pid}-c{col}-{seq[col]:03d}",
                "page_id": info["page_id"], "printed_page": info["printed_page"],
                "column": col, "seq": seq[col],
                "street_name": bold[1].strip(" ,"), "continued": False,
                "_first": strip_bold(line),
            }
            streets.append(current)
            desc_lines = [bold[2]] if bold[2] else []
            in_table, labels = False, ("", "")
            continue
        if line.startswith("Cross Sts"):
            parts = [p.strip(".") for p in line.split()[2:]]
            labels = (parts + ["", ""])[:2]
            in_table = True
            continue
        row = parse_row(line) if in_table else None
        if row:
            name, a, b = row
            crossings.append({
                "street_id": current["street_id"] if current else "",
                "street_name": current["street_name"] if current else "",
                "page_id": info["page_id"],
                "seq": sum(1 for c in crossings if c["street_id"] == (current or {}).get("street_id", "")) + 1,
                "cross_street": name,
                "side_a_label": labels[0], "side_b_label": labels[1],
                "side_a_raw": a, "side_b_raw": b,
                "side_a_number": a if a.isdigit() else "",
                "side_b_number": b if b.isdigit() else "",
                "continued": current is None,
            })
        elif current is not None:
            desc_lines.append(line)
    close_street()
    return streets, crossings


# ------------------------------------------------------------ classified pages

def load_classified(info, model):
    path = ROOT / "transcripts" / model / info["page_id"] / "classified.json"
    if not path.exists():
        print(f"  {info['page_id']}: no classified.json yet (run scripts/extract_classified.py)")
        return [], []
    data = json.loads(path.read_text())
    orgs, officers = [], []
    pid = short_id(info["page_id"])
    for i, o in enumerate(data["organizations"], 1):
        org_id = f"{pid}-{i:03d}"
        orgs.append({
            "org_id": org_id, "page_id": info["page_id"], "printed_page": info["printed_page"],
            "section": o["section"], "name": o["name"], "address": o["address"],
            "organized": o["organized"], "incorporated": o["incorporated"],
            "financials": o["financials"], "see_reference": o["see_reference"],
            "continued": o["continued_from_previous_page"],
            "continues_next_page": o["continues_on_next_page"],
            "raw_text": o["raw_text"],
        })
        for p in o["people"]:
            officers.append({"org_id": org_id, "person_name": p["name"], "role": p["role"],
                             "role_organization": p.get("role_organization", "")})
    return orgs, officers


# ------------------------------------------------------------------------ main

def write_csv(out_dir, name, rows):
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / name
    if not rows:
        path.unlink(missing_ok=True)
        return
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {path.relative_to(ROOT)}: {len(rows)} rows")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="routed",
                    help="'routed' (each page from its routed model) or one model's folder")
    ap.add_argument("--out", type=Path, default=TABLES, help="folder for the CSV files")
    args = ap.parse_args()
    out = args.out if args.out.is_absolute() else ROOT / args.out

    transcripts = ROOT / "transcripts"
    if args.model == "routed":
        routing_file = transcripts / "routing.json"
        if not routing_file.exists():
            raise SystemExit("No transcripts/routing.json yet. Run scripts/pipeline.py first.")
        routing = json.loads(routing_file.read_text())
        sources = [(transcripts / r["model"] / page_id, r["model"]) for page_id, r in routing.items()]
    else:
        sources = [(d, args.model) for d in sorted((transcripts / args.model).iterdir()) if d.is_dir()]

    pages, entries, streets, crossings, orgs, officers = [], [], [], [], [], []
    for page_dir, model in sources:
        header, lines = load_page(page_dir)
        info = classify_page(page_dir.name, header, lines)
        info["transcribed_by"] = model
        pages.append(info)
        if info["page_type"] == "alphabetical":
            entries += parse_alphabetical(info, lines)
        elif info["page_type"] == "street_guide":
            s, c = parse_street_guide(info, lines)
            streets += s
            crossings += c
        else:
            o, p = load_classified(info, "claude-sonnet-5-5")  # extraction is always Sonnet
            orgs += o
            officers += p

    write_csv(out, "pages.csv", pages)
    write_csv(out, "directory_entries.csv", entries)
    write_csv(out, "streets.csv", streets)
    write_csv(out, "street_crossings.csv", crossings)
    write_csv(out, "organizations.csv", orgs)
    write_csv(out, "officers.csv", officers)


if __name__ == "__main__":
    main()
