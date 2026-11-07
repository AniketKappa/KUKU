"""Golden checks: does the parsed text at a known location say what the law says?

These facts were chosen independently of the parser. If a parser change shifts text
between sections, these fail, which stops the library silently mis-attributing law.
"""
import json
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
RECS = {}
for line in (ROOT / "corpus" / "provisions.jsonl").read_text().splitlines():
    r = json.loads(line)
    RECS[r["id"]] = r

# Section counts are public facts about each Act.
EXPECTED_LAST = {"CPA2019": "107", "COW2019": "69", "IRC2020": "104", "SSC2020": "164",
                 "BNS2023": "358", "BNSS2023": "531", "BSA2023": "170", "TPA1882": "137",
                 "LIM1963": "32", "CPC1908": "158"}

GOLDEN = [
    ("CPA2019:s2", ["deficiency", "unfair trade practice"]),
    ("CPA2019:s34", ["District Commission", "one crore"]),
    ("CPA2019:s35", ["complaint"]),
    ("CPA2019:s69", ["two years"]),
    ("CPJUR2021:r3", ["fifty lakh"]),
    ("COW2019:s17", ["seventh day", "two working days"]),
    ("COW2019:s45", ["three years"]),
    ("COW2019:s69", ["Payment of Wages Act"]),
    ("IRC2020:s104", ["Industrial Disputes Act"]),
    ("SSC2020:s53", ["gratuity", "five years"]),
    ("ICA1872:s73", ["compensation"]),
    ("TPA1882:s105", ["lease"]),
    ("TPA1882:s106", ["fifteen days"]),
    ("LIM1963:s3", ["dismissed"]),
    ("LIM1963:art55", ["breach", "Three years"]),
    ("NIA1881:s138", ["cheque", "thirty days", "fifteen days"]),
    ("NIA1881:s142", ["one month"]),
    ("LSA1987:s12", ["legal services"]),
    ("BNS2023:s318", ["cheat"]),
    ("BNS2023:s358", ["Indian Penal Code"]),
    ("BNSS2023:s531", ["Code of Criminal Procedure"]),
    ("BSA2023:s63", ["electronic record"]),
    ("BSA2023:s170", ["Indian Evidence Act"]),
    ("CPC1908:o37", ["summary", "promissory"]),
    ("MTA2021:s11", ["security deposit", "two months"]),
    ("TNRRA2017:s11", ["security deposit"]),
]


@pytest.mark.parametrize("pid,phrases", GOLDEN)
def test_golden_text(pid, phrases):
    assert pid in RECS, f"{pid} missing from corpus"
    low = " ".join(RECS[pid]["text"].lower().split())
    for phrase in phrases:
        assert phrase.lower() in low, f"{pid} lacks {phrase!r}: {low[:300]}"


@pytest.mark.parametrize("sid,last", EXPECTED_LAST.items())
def test_act_reaches_expected_final_section(sid, last):
    assert f"{sid}:s{last}" in RECS


@pytest.mark.parametrize("sid,minimum", [("BNS2023", 358), ("BSA2023", 170), ("CPA2019", 107), ("COW2019", 69),
                                         ("LIM1963", 32 + 137), ("BNSS2023", 531), ("IRC2020", 104), ("SSC2020", 164)])
def test_no_sections_lost(sid, minimum):
    assert sum(1 for r in RECS.values() if r["source_id"] == sid) >= minimum


def test_no_bills_in_corpus():
    for r in RECS.values():
        assert "clause" not in r["unit"].lower()


def test_every_record_is_traceable():
    for r in RECS.values():
        assert r["url"].startswith("http") and len(r["sha256"]) == 64 and r["page"] >= 1 and r["retrieved"]
