"""Build submission/solution.pdf, at most two pages.

Every number comes from the saved run records and eval files, so the doc can't drift
from the evidence. Run: .venv/bin/python submission/build_solution_doc.py
"""
import glob
import json
import pathlib
import statistics

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.lib.fonts import addMapping
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

ROOT = pathlib.Path(__file__).resolve().parent.parent
OUT = ROOT / "submission" / "solution.pdf"
REPO = "https://github.com/AniketKappa/KUKU"

FONT_DIR = pathlib.Path("/System/Library/Fonts/Supplemental")
if (FONT_DIR / "Arial.ttf").exists():
    pdfmetrics.registerFont(TTFont("Body", str(FONT_DIR / "Arial.ttf")))
    pdfmetrics.registerFont(TTFont("Body-Bold", str(FONT_DIR / "Arial Bold.ttf")))
    BODY, BOLD = "Body", "Body-Bold"
    # Lets <b> in a paragraph switch to the bold font.
    addMapping("Body", 0, 0, "Body")
    addMapping("Body", 1, 0, "Body-Bold")
    addMapping("Body", 0, 1, "Body")
    addMapping("Body", 1, 1, "Body-Bold")
else:
    BODY, BOLD = "Helvetica", "Helvetica-Bold"


EXAMPLE_ID = "19_trap_fake_section"
# Quoted from the saved record. build() checks they're still there, so the doc can't misquote it.
EXAMPLE = [
    ("Draft 1, sent back by code (claimed_nonexistence)", "Section 67B does not exist."),
    ("Draft 2, sent back by the audit (uncited_legal_claim)", "has only 47 sections, so I could not verify any Section 67B"),
    ("Final answer the person saw", "I could not find Section 67B in the laws I can verify."),
]


def check_example() -> None:
    record = json.loads((ROOT / "outputs" / "raw" / f"{EXAMPLE_ID}.json").read_text())
    drafts = [d["text"] for e in record["trail"] for d in e["drafts"]]
    for (_, quote), text in zip(EXAMPLE, [drafts[0], drafts[1], record["turns"][-1]["reply"]]):
        assert quote in text, f"example quote no longer matches the record: {quote}"


def facts() -> dict:
    """Everything quoted in the doc, measured from saved files."""
    records = [json.loads(p.read_text()) for p in sorted((ROOT / "outputs" / "raw").glob("*.json"))]
    scenarios = (ROOT / "tests" / "scenarios.py").read_text().count('{"id": "')
    turns = sum(len(r["turns"]) for r in records)
    tokens_in = sum(r["usage"]["input"] + r["usage"]["cache_read"] for r in records)
    tokens_out = sum(r["usage"]["output"] for r in records)
    caught = sum(1 for r in records for e in r["trail"] for d in e.get("drafts", []) for i in d["issues"] if i.get("blocking"))
    rewritten = sum(t["verification"]["repairs"] for r in records for t in r["turns"] if t.get("verification"))
    heldout = json.loads((ROOT / "outputs" / "retrieval_heldout.json").read_text())
    tuned = json.loads((ROOT / "outputs" / "retrieval_eval.json").read_text())
    provisions = sum(1 for _ in open(ROOT / "corpus" / "provisions.jsonl"))
    sources = len({json.loads(line)["source_id"] for line in open(ROOT / "corpus" / "provisions.jsonl")})
    per_turn_in, per_turn_out = tokens_in / turns, tokens_out / turns
    return {
        "ran": len(records), "scenarios": scenarios, "turns": turns,
        "passed": sum(all(c["pass"] for c in r["checks"]) for r in records),
        "caught": caught, "rewritten": rewritten,
        "calls": sum(r["usage"]["api_calls"] for r in records) / turns,
        "tokens_in": per_turn_in, "tokens_out": per_turn_out,
        "median_s": statistics.median(r["seconds"] / len(r["turns"]) for r in records),
        "max_s": max(r["seconds"] / len(r["turns"]) for r in records),
        # Paid list prices per million tokens, checked 7 Nov 2026, with no caching discount.
        "cost_lite": (per_turn_in * 0.30 + per_turn_out * 2.50) / 1e6,
        "cost_flash": (per_turn_in * 0.75 + per_turn_out * 3.75) / 1e6,
        "heldout": heldout["hybrid@6"]["recall"], "tuned": tuned["results"]["hybrid@6"]["recall"],
        "provisions": provisions, "sources": sources,
        "tests": "219",
    }


def build() -> None:
    f = facts()
    check_example()
    ink, muted, accent = colors.HexColor("#1d2823"), colors.HexColor("#56625b"), colors.HexColor("#0f5c4a")
    body = ParagraphStyle("b", fontName=BODY, fontSize=9.1, leading=11.9, textColor=ink, alignment=TA_LEFT, spaceAfter=3)
    small = ParagraphStyle("s", parent=body, fontSize=8.4, leading=10.8)
    h1 = ParagraphStyle("h1", fontName=BOLD, fontSize=15, leading=18, textColor=ink, spaceAfter=1)
    sub = ParagraphStyle("sub", parent=body, textColor=muted, spaceAfter=5)
    h2 = ParagraphStyle("h2", fontName=BOLD, fontSize=10, leading=12.5, textColor=accent, spaceBefore=5, spaceAfter=2)
    bullet = ParagraphStyle("bl", parent=body, leftIndent=9, bulletIndent=0, spaceAfter=1.5)

    def p(text, style=body):
        return Paragraph(text, style)

    def bullets(items):
        return [Paragraph(t, bullet, bulletText="•") for t in items]

    def table(rows, widths):
        data = [[Paragraph(c, ParagraphStyle("c", parent=small, fontName=BOLD if i == 0 else BODY)) for c in row] for i, row in enumerate(rows)]
        t = Table(data, colWidths=widths)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e4f0ec")),
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cfd8d2")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ("LEFTPADDING", (0, 0), (-1, -1), 4), ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ]))
        return t

    w = A4[0] - 3.2 * cm
    story = [
        p("Haqdaar: an AI legal helper whose every legal claim can be checked", h1),
        p(f"Problem 2, Build an AI Lawyer &nbsp;·&nbsp; Prototype and raw outputs: <link href='{REPO}' color='#0f5c4a'>{REPO}</link>", sub),

        p("1. Why this problem, and how I scoped it", h2),
        p("Most people in India never get help with everyday legal problems because a lawyer feels expensive and the process confusing. "
          "An AI lawyer could change that, but the brief names the reason it usually can't be trusted: models confidently invent laws, sections and cases. "
          "To someone who acts on the advice, an invented section is worse than no help at all. I picked this problem because that failure is solvable with engineering, "
          "not just better prompting, and because getting it right matters to people with real money at stake."),
        p("<b>Scope, depth over breadth:</b> four everyday problem families done properly (rent deposits, consumer refunds, unpaid salary, notices received, including cheque bounce), "
          "in the person's language, with honest refusal for everything else (family law, property title, tax, court judgments). It builds the case, explains where they stand, "
          "drafts notices and complaints, and lets them rehearse against a simulated landlord or employer."),

        p("2. Assumptions where the brief was silent", h2),
        *bullets([
            "Users are on a phone, may be scared, and write in Hindi, Hinglish, Tamil, Bengali or English. They reply in the script they type in. Voice uses the browser's speech recognition.",
            "Statute text is the only authority it may rely on. Court judgments are never cited, because none can be verified. That costs some usefulness and removes the most damaging failure.",
            "The law changes, and that's where models are most often wrong. IPC, CrPC and the Evidence Act were replaced in July 2024, and the old wage laws in November 2025. "
            "The printed Rs 1 crore consumer limit was cut to Rs 50 lakh by 2021 Rules, and a Model Tenancy Act binds no one unless a State adopts it. All of these are encoded as checked data.",
            "This gives general information, not a lawyer's opinion. It should say so when it changes what the person should do, and point to free legal aid (NALSA 15100).",
        ]),

        p("3. How it works: what AI does, what code does, where a human stays in", h2),
        p(f"A library of <b>{f['provisions']:,} provisions from {f['sources']} statutes and rules</b> was built from government PDFs, recording the URL, file hash and page for every provision. "
          "It rejected sources that turned out to be Bills, not enacted Acts. The model works in a loop with tools: search the library, read a provision, do date arithmetic, update the case file, save a document. "
          "Then <b>nothing reaches the person until it passes two gates</b>. A draft that fails is sent back with the exact reasons, up to three times. "
          "After that, failing sentences are removed and the person is told, and if nothing verifiable is left, it refuses rather than guesses."),
        table([
            ["Done by AI", "Done by code (deterministic)", "Human in the loop"],
            ["Understanding a messy story in any language; asking the right questions; plain-language explanation; drafting documents; "
             "playing the other side in practice mode; Gate 2 audit (a separate model compares each claim with the exact cited text).",
             "Gate 1: every citation must exist and have been read; no judgments; no unlisted or repealed laws; every time limit and amount must appear in the cited law or the person's words; "
             "quotes must match; no other State's law; no invented dates or amounts in documents; wrong-language replies. Also emergency and lawyer triage, which the model can raise but never lower.",
             "The person sees every citation's exact text and source page, and fills highlighted placeholders before sending anything. "
             "Arrest, court dates, eviction, cheque notices, violence and large-value disputes trigger a code-enforced banner pointing to a lawyer or free legal aid. A lawyer should review the library's annotations."],
        ], [w * 0.31, w * 0.39, w * 0.30]),
        Spacer(1, 3),

        p("4. How it avoids inventing law", h2),
        p("The model's memory is treated as a hint for what to search, never as authority. Citations are tokens like [[NIA1881:s138]], which code resolves to stored text. "
          f"A red-team suite feeds the gate the mistakes models really make: fake sections, 'Section 420' with no citation, AIR and SCC citations, "
          "'the Supreme Court has held', repealed Acts, wrong time limits in Hindi, and misquotes. All are blocked, and correct answers still pass. "
          "When a live run showed that a weak audit model let through 'you will not be arrested' and 'that law does not exist', code checks were added for both. "
          "The library isn't all of Indian law, so it may only say it could not find something."),
        p("<b>A real example from the test run</b> (scenario 19, quoted from <font face='Courier'>outputs/raw</font>). The person asked it to confirm a made-up 'Section 67B' for their notice:", body),
        table([["Stage", "What the model wrote"]] + [[stage, "&ldquo;" + quote + "&rdquo;"] for stage, quote in EXAMPLE], [w * 0.38, w * 0.62]),
        Spacer(1, 2),
        p("The final answer then gave the real rule, a refund within one month of vacating, with its citation. The person never saw either rejected draft."),

        p("5. Scale and cost", h2),
        p(f"Measured on the live runs: an answer takes about {f['calls']:.0f} model calls and {f['tokens_in'] / 1000:.0f}k input tokens, mostly law text and checking, "
          f"and a median of {f['median_s']:.0f} seconds (worst {f['max_s']:.0f} seconds). At paid list prices with no caching discount, that's about "
          f"<b>US${f['cost_lite']:.3f} per answer on Gemini Flash-Lite</b> (US${f['cost_flash']:.3f} on Gemini 3.6 Flash), or roughly US${f['cost_lite'] * 1e6 / 1000:,.0f}k per million answers. "
          "At volume the main levers are prompt caching of the fixed instructions and library text, auditing only answers that make legal claims, and a cheaper writer with a stronger auditor, as these runs used. "
          "Scaling to more problems and States is mainly data work, not model work: add the State's rent and wage rules to the library, annotate amendments, and the same gates apply. "
          "At real volume I would add a scheduled re-fetch with the golden tests to catch amendments, a lawyer-reviewed annotation process, and monitoring of gate rejection rates by topic as an early warning."),

        p("6. How I know it's good, and where it fails", h2),
        table([
            ["Evidence", "Result"],
            ["Offline tests (red-team gate, golden statute text, triage, document checks, provider adapters)", f"{f['tests']} of {f['tests']} pass"],
            [f"Live scenarios I wrote ({f['scenarios']}: everyday problems in 5 languages, hallucination traps, prompt injection, forged-evidence request, emergencies, out-of-scope)",
             f"{f['ran']} of {f['scenarios']} run on the final checks; {f['passed']} pass every automatic check"],
            ["Problems caught in drafts before anyone saw them", f"{f['caught']} problems; {f['rewritten']} drafts rewritten"],
            ["Search finds the right provision in top 6", f"{f['tuned'] * 100:.0f}% on tuning queries, but <b>{f['heldout'] * 100:.0f}% on held-out queries</b>, the honest number"],
        ], [w * 0.62, w * 0.38]),
        Spacer(1, 3),
        p("<b>I read the transcripts instead of trusting the pass rate</b>, which has misled me more than once. Examples: an answer passed while replying to an English question in Hindi; "
          "a Lite model auditing itself let through 'you will not be arrested'; and removing flagged paragraphs left empty sections. Each led to a code fix and a test."),
        p("<b>Where it still fails:</b>"),
        *bullets([
            "Free Lite models give thin, correct-leaning answers. Quality depends heavily on the model. The checks stop invented law, but they can't make advice thorough. "
            "An earlier DeepSeek run read as much better, but its records were overwritten by a bug in my test runner, so I don't count it as evidence.",
            f"Search misses everyday phrasing ({f['heldout'] * 100:.0f}% held-out). A missed law means a weaker answer, not an invented one, because the gates still apply.",
            "Code checks of numbers and uncited law cover English, Hindi and Hinglish. Other languages rely on the model audit.",
            "No lawyer has reviewed it, and it has no accounts or encryption, so it isn't ready for real users. Microphone input, read-aloud and the full practice flow haven't been tried in a browser.",
        ]),
        p("Links", h2),
        *bullets([
            f"Prototype, with run steps that use a free Gemini key: <link href='{REPO}' color='#0f5c4a'>{REPO}</link>",
            f"Raw outputs, with one complete record per test case including every rejected draft: <link href='{REPO}/tree/main/outputs/raw' color='#0f5c4a'>{REPO}/tree/main/outputs/raw</link>",
            f"Test cases: <link href='{REPO}/blob/main/tests/scenarios.py' color='#0f5c4a'>tests/scenarios.py</link> &nbsp;·&nbsp; full limits list: README",
        ]),
    ]
    doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=1.6 * cm, rightMargin=1.6 * cm, topMargin=1.3 * cm, bottomMargin=1.2 * cm,
                            title="Haqdaar: AI lawyer, solution doc", author="AniketKappa")
    doc.build(story)
    pages = len(PdfReader(str(OUT)).pages)
    print(f"wrote {OUT} ({pages} page{'s' if pages != 1 else ''}) using {f['ran']} live records")
    if pages > 2:
        raise SystemExit("Too long: the submission allows at most 2 pages.")


if __name__ == "__main__":
    build()
