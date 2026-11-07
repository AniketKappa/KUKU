"""Turn the downloaded statute PDFs into corpus/provisions.jsonl.

One record per legal provision (section, rule, Limitation Act article, CPC order).
Every record keeps the page it came from and the source file's URL and hash, so a
citation in an answer can always be traced back to a specific page of a specific file.

Parsing notes:
- Gazette PDFs put section headings in 8pt margin notes. They are separated from
  the 10pt body by font size and matched to the section by vertical position.
- False "section starts" (list items, footnotes) are removed by taking the longest
  increasing subsequence of candidate numbers instead of per-Act rules.
- No text is ever rewritten by a model. It is the PDF's text layer, lightly cleaned.
"""
import json
import pathlib
import re
import sys
from bisect import bisect_left

from pypdf import PdfReader

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "corpus" / "raw"

DEVANAGARI = re.compile(r"[ऀ-ॿ]")
GLYPH = re.compile(r"/G(\d{2,3})")
SEC_RE = re.compile(r"^\s*(?:\d?\[)?(\d{1,3})([A-Z]{0,3})\s?\.\s*(.*)$")
FOOTNOTE_RE = re.compile(
    r"^\s*\d{1,3}\.\s*(?:Subs|Ins|Omitted|Added|Rep\.|The words|Cl\.|Sub-s|S\.|Ss\.|Section \d|Clause|"
    r"Certain words|For the words|Now see|This Act|Extended|See )", re.I)
HEADER_RE = re.compile(
    r"(GAZETTE\s+OF\s+INDIA|GOVERNMENT\s+GAZETTE|^\s*Page \d+ of \d+\s*$|^\s*\d{1,3}\s*$|^\s*\(?[ivxlc]+\)?\s*$|"
    r"PRS Legislative|DISCLAIMER|^\s*SEC\. ?\d|\[P\s?ART)", re.I)


def fix_spacing(line):
    """Undo justified-text extraction: ')I f  i n  a n y' -> ')If in any'."""
    tokens = line.split(" ")
    singles = sum(1 for t in tokens if len(t) == 1)
    if singles >= 6 and singles > 0.5 * len([t for t in tokens if t]):
        words = re.split(r"\s{2,}", line)
        return " ".join(w.replace(" ", "") for w in words)
    return line


def page_lines(page):
    """Return (body_lines, margin_groups) for a page, using font size to split them."""
    frags = []

    def visit(text, cm, tm, _font, size):
        if text:
            y = tm[5] * cm[3] + cm[5]
            x = tm[4] * cm[0] + cm[4]
            scale = abs(tm[0] or 1) * abs(cm[0] or 1)
            frags.append((text, x, y, size * scale))

    try:
        page.extract_text(visitor_text=visit)
    except Exception:
        frags = []
    width = float(page.mediabox.width)
    body, margins, cur, cur_y = [], [], [], None
    for text, x, y, size in frags:
        is_margin = 0 < size <= 8.6 and (x < 0.18 * width or x > 0.75 * width)
        if is_margin:
            if text.strip():
                margins.append((y, text.strip()))
            continue
        for i, part in enumerate(text.split("\n")):
            if i > 0 or (cur_y is not None and abs(y - cur_y) > 4 and part.strip()):
                if cur:
                    body.append(("".join(cur), cur_y if cur_y is not None else y))
                cur = []
                cur_y = None
            if part:
                cur.append(part)
                if cur_y is None:
                    cur_y = y
    if cur:
        body.append(("".join(cur), cur_y if cur_y is not None else 0))
    # Group margin fragments into notes by vertical proximity.
    groups = []
    for y, text in sorted(margins, key=lambda t: -t[0]):
        if groups and groups[-1]["last"] - y < 11.5:
            groups[-1]["text"] += " " + text
            groups[-1]["last"] = y
        else:
            groups.append({"top": y, "last": y, "text": text})
    return body, groups


def clean_line(text):
    text = GLYPH.sub(lambda m: chr(int(m.group(1))), text)
    text = text.replace(" ", " ").replace("ﬁ", "fi").replace("ﬂ", "fl")
    return fix_spacing(text).rstrip()


def extract(entry):
    reader = PdfReader(str(RAW / f"{entry['id']}.pdf"))
    lines, margins_by_page = [], {}
    for pno, page in enumerate(reader.pages, 1):
        body, groups = page_lines(page)
        margins_by_page[pno] = groups
        for raw, y in body:
            for piece in raw.split("\n"):
                t = clean_line(piece)
                if not t.strip() or DEVANAGARI.search(t):
                    continue
                if HEADER_RE.search(t) or FOOTNOTE_RE.match(t):
                    continue
                lines.append({"t": t, "page": pno, "y": y})
    return lines, margins_by_page


def lis(cands):
    """Longest strictly increasing subsequence by (number, suffix)."""
    keys = [(c["num"], c["suffix"]) for c in cands]
    tails, tail_idx, prev = [], [], [-1] * len(keys)
    for i, k in enumerate(keys):
        pos = bisect_left(tails, k)
        if pos:
            prev[i] = tail_idx[pos - 1]
        if pos == len(tails):
            tails.append(k)
            tail_idx.append(i)
        else:
            tails[pos] = k
            tail_idx[pos] = i
    out, i = [], tail_idx[-1] if tail_idx else -1
    while i >= 0:
        out.append(cands[i])
        i = prev[i]
    return out[::-1]


def split_heading(rest):
    """'Cheating.—(1) Whoever...' -> ('Cheating', '(1) Whoever...'). '(1) Whoever' -> ('', ...)."""
    if rest.startswith("(") or not rest:
        return "", rest
    m = re.match(r"^(.{3,320}?)\s*[.:]?\s*(?:[—–]+|\.\s?-+|-+\s)\s*(.*)$", rest, re.S)
    if m and not re.search(r"\b(shall|may be called|whoever)\b|\(\d\)|^In this |,$", m.group(1).strip(), re.I):
        return re.sub(r"\s+", " ", m.group(1)).strip(" .:"), m.group(2)
    # Heading alone on its own line: "18. Recovery of possession for occupation."
    first, _, remainder = rest.partition("\n")
    if remainder and len(first) < 170 and first.rstrip().endswith((".", ".-", "-")) \
            and not re.search(r"\b(shall|whoever)\b", first, re.I):
        return first.strip(" .:-"), remainder
    return "", rest


def arrangement_headings(lines):
    """Read the Act's own table of contents: {'11': 'Security deposit'}."""
    out, last = {}, None
    for l in lines:
        t = l["t"].strip()
        m = SEC_RE.match(t)
        if m and not m.group(3).startswith("("):
            key = m.group(1) + m.group(2)
            last = key if key not in out else None
            if last:
                out[last] = m.group(3)
            continue
        if re.match(r"^(CHAPTER|PART|SECTIONS?|CLAUSES?|RULES?|PRELIMINARY|THE )\b", t) or t.isupper():
            last = None
        elif last and not out[last].rstrip().endswith("."):
            out[last] += " " + t
    return {k: re.sub(r"\s+", " ", v).strip(" .") for k, v in out.items()}


def margin_heading(margins, page, y):
    best, dist = "", 22
    for g in margins.get(page, []):
        d = abs(g["top"] - y)
        if d < dist:
            best, dist = g["text"], d
    return re.sub(r"\s+", " ", best).replace("- ", "").strip(" .")


def parse_sections(entry, lines, margins):
    start = next((i for i, l in enumerate(lines)
                  if re.search(r"may\s+be\s+(called|cited)|This Act may|These rules may", l["t"], re.I)), 0)
    back = start
    for j in range(start, max(start - 6, -1), -1):
        m = SEC_RE.match(lines[j]["t"])
        if m and m.group(1) == "1":
            back = j
            break
    toc = arrangement_headings(lines[:back]) if back > 5 else {}
    body = lines[back:]
    stop = len(body)
    if entry.get("stop_at"):
        for i, l in enumerate(body):
            if i > 50 and re.search(entry["stop_at"], l["t"]):
                stop = i
                break
    sched = None
    for i in range(0 if entry.get("schedule_at") else int(stop * 0.2), stop):
        if re.match(entry.get("schedule_at") or r"^\s*(\d?\[?)(THE\s+)?(FIRST\s+)?SCHEDULE\b", body[i]["t"]):
            sched = i
            break
    end = sched if sched is not None else stop
    cands = []
    for i in range(end):
        m = SEC_RE.match(body[i]["t"])
        if not m:
            continue
        cands.append({"i": i, "num": int(m.group(1)), "suffix": m.group(2), "rest": m.group(3)})
    chain = lis(cands)
    records = []
    for k, c in enumerate(chain):
        stop_i = chain[k + 1]["i"] if k + 1 < len(chain) else end
        joined = "\n".join([c["rest"]] + [body[j]["t"].strip() for j in range(c["i"] + 1, stop_i)])
        heading, text = split_heading(joined)
        text = re.sub(r"[ \t]+", " ", text).strip()
        # A chapter title printed before the next section is not part of this one.
        text = re.sub(r"(\n(CHAPTER|PART)\s+[IVXLC]+[\s\S]{0,160})$", "", text).strip()
        loc = body[c["i"]]
        if not heading:
            heading = margin_heading(margins, loc["page"], loc["y"])
        if not heading:
            heading = toc.get(f"{c['num']}{c['suffix']}", "")
        records.append({"number": f"{c['num']}{c['suffix']}", "heading": heading, "text": text, "page": loc["page"]})
    tail = body[sched:stop] if sched is not None else []
    return records, tail


def parse_limitation_articles(tail):
    """Limitation Act schedule: 'N. For money payable... Three years. When...'"""
    cands = []
    for i, l in enumerate(tail):
        m = re.match(r"^\s*(\d{1,3})\s*\.\s+(.*)$", l["t"])
        if m:
            cands.append({"i": i, "num": int(m.group(1)), "suffix": "", "rest": m.group(2)})
    chain = lis(cands)
    out = []
    for k, c in enumerate(chain):
        stop = chain[k + 1]["i"] if k + 1 < len(chain) else len(tail)
        text = " ".join([c["rest"]] + [tail[j]["t"].strip() for j in range(c["i"] + 1, stop)])
        text = re.sub(r"\s+", " ", text).strip()
        out.append({"number": str(c["num"]), "heading": "Schedule article", "text": text, "page": tail[c["i"]]["page"]})
    return out


ROMAN = {"M": 1000, "D": 500, "C": 100, "L": 50, "X": 10, "V": 5, "I": 1}


def roman_to_int(s):
    total = 0
    for a, b in zip(s, s[1:] + " "):
        v = ROMAN[a]
        total += -v if b in ROMAN and ROMAN[b] > v else v
    return total


def parse_cpc_orders(tail):
    """CPC First Schedule. Each order starts 'ORDER XXXVII-SUMMARY PROCEDURE'; the
    heading can repeat as a running header, so repeats extend the same order."""
    heads = []
    for i, l in enumerate(tail):
        m = re.match(r"^\s*\d?\[?ORDER\s+([IVXLC]+)([A-Z]?)\s*[-—–]?\s*(.*)$", l["t"].strip())
        if m and (m.group(2) == "" or m.group(2) == "A"):
            heads.append((i, f"{roman_to_int(m.group(1))}{m.group(2)}", m.group(3).strip(), l["page"]))
    orders = {}
    for k, (i, num, title, page) in enumerate(heads):
        stop = heads[k + 1][0] if k + 1 < len(heads) else len(tail)
        o = orders.setdefault(num, {"number": num, "heading": title.title(), "lines": [], "page": page})
        if title and not o["heading"]:
            o["heading"] = title.title()
        o["lines"] += [tail[j]["t"].strip() for j in range(i + 1, stop)]
    return [{"number": o["number"], "heading": o["heading"], "page": o["page"],
             "text": re.sub(r"[ \t]+", " ", "\n".join(o["lines"]))} for o in orders.values() if len(o["lines"]) > 2]


def main():
    manifest = json.loads((ROOT / "corpus" / "manifest.json").read_text())
    prov = json.loads((ROOT / "corpus" / "provenance.json").read_text())
    out_path = ROOT / "corpus" / "provisions.jsonl"
    report, total = [], 0
    only = set(sys.argv[1:])
    with out_path.open("w") as out:
        for entry in manifest:
            sid = entry["id"]
            if sid not in prov or (only and sid not in only):
                report.append(f"{sid:10} SKIPPED")
                continue
            lines, margins = extract(entry)
            sections, tail = parse_sections(entry, lines, margins)
            prefix = "r" if entry["unit"] == "Rule" else "s"
            rows = [(f"{sid}:{prefix}{s['number']}", entry["unit"], s) for s in sections]
            if sid == "LIM1963":
                rows += [(f"{sid}:art{a['number']}", "Article", a) for a in parse_limitation_articles(tail)]
            if sid == "CPC1908":
                rows += [(f"{sid}:o{o['number']}", "Order", o) for o in parse_cpc_orders(tail)]
            elif tail and sid != "LIM1963":
                text = re.sub(r"[ \t]+", " ", "\n".join(l["t"].strip() for l in tail))
                rows.append((f"{sid}:sch", "Schedule", {"number": "Schedule", "heading": "Schedule", "text": text, "page": tail[0]["page"]}))
            for pid, unit, s in rows:
                out.write(json.dumps({
                    "id": pid, "source_id": sid, "act": entry["title"], "act_short": entry["short"],
                    "unit": unit, "number": s["number"], "heading": s["heading"], "text": s["text"],
                    "page": s["page"], "url": prov[sid]["url"], "retrieved": prov[sid]["retrieved"],
                    "sha256": prov[sid]["sha256"], "status": entry["status"],
                    "jurisdiction": entry["jurisdiction"], "domain": entry["domain"],
                    "mirror": not re.search(r"https?://[^/]*(gov\.in|nic\.in)/", prov[sid]["url"]),
                    "text_quality": entry.get("text_quality", "text"),
                    "repealed": len(s["text"]) < 400 and bool(re.search(r"\[?(Repealed|Rep\.|Omitted)", s["text"] + " " + s["heading"])),
                }, ensure_ascii=False) + "\n")
            total += len(rows)
            nh = sum(1 for _, _, s in rows if s["heading"])
            last = sections[-1]["number"] if sections else "-"
            report.append(f"{sid:10} provisions={len(rows):4} last_section={last:>5} with_heading={nh:4} tail_lines={len(tail)}")
    print("\n".join(report))
    print("TOTAL", total)


if __name__ == "__main__":
    main()
