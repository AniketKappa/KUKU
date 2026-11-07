"""Held-out search test. These queries were written BEFORE the glossary and topic map.

The tuning used tests/retrieval_eval.py, so scoring on that set alone would flatter it.
Do not add these words to the glossary to raise this score: that would defeat the test.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from app.retrieval import index  # noqa: E402

HELD_OUT = [
    ("owner kept my advance when I left the flat", "Tamil Nadu", ["TNRRA2017:s11"], "en"),
    ("can the house owner refuse to give back the advance money", "Tamil Nadu", ["TNRRA2017:s11"], "en"),
    ("mera advance wapas nahi de raha owner", "Tamil Nadu", ["TNRRA2017:s11"], "hinglish"),
    ("how many days notice must a tenant give before leaving", None, ["TPA1882:s106"], "en"),
    ("does landlord need a written rent agreement", "Karnataka", ["KRA1999:s4"], "en"),
    ("can landlord throw me out without court order", "Delhi", ["DRC1958:s14"], "en"),
    ("paid for a phone that never arrived and seller is ignoring me", None, ["CPECOM2020:r6", "CPECOM2020:r7", "CPA2019:s35", "CPA2019:s2"], "en"),
    ("airline cancelled flight and no money back", None, ["CPA2019:s2", "CPA2019:s35", "CPA2019:s39"], "en"),
    ("how much fee to file a case in consumer court", None, ["CPCOMM2020:r7"], "en"),
    ("can I appeal if I lose in consumer forum", None, ["CPA2019:s41"], "en"),
    ("is it too late, I bought this three years ago, consumer case", None, ["CPA2019:s69"], "en"),
    ("how quickly must an online store answer my complaint", None, ["CPECOM2020:r4", "CPECOM2020:r6"], "en"),
    ("उपभोक्ता शिकायत कहां दर्ज करें", None, ["CPA2019:s35", "CPA2019:s34"], "hi"),
    ("boss is not giving my pay since January", None, ["COW2019:s17", "COW2019:s45"], "en"),
    ("I quit my job, when will I get my last pay", None, ["COW2019:s17"], "en"),
    ("company cut money from my pay without reason", None, ["COW2019:s18"], "en"),
    ("what happens to an employer who does not pay staff", None, ["COW2019:s54"], "en"),
    ("meri naukri chali gayi aur company ne paisa nahi diya", None, ["COW2019:s17", "COW2019:s45"], "hinglish"),
    ("नौकरी छोड़ने के बाद ग्रेच्युटी", None, ["SSC2020:s53"], "hi"),
    ("someone gave me a cheque and the bank returned it", None, ["NIA1881:s138"], "en"),
    ("I got a legal notice about a cheque, what must I do", None, ["NIA1881:s138"], "en"),
    ("can the director of a company be liable for a bad cheque", None, ["NIA1881:s141"], "en"),
    ("I cannot afford a lawyer", None, ["LSA1987:s12", "LSA1987:s13"], "en"),
    ("he took money and vanished, is it a crime", None, ["BNS2023:s318", "BNS2023:s316"], "en"),
    ("can I record a call and use it as proof", None, ["BSA2023:s63", "BSA2023:s61"], "en"),
    ("how do I complain to police about a crime", None, ["BNSS2023:s173"], "en"),
]


def main():
    idx = index()
    report = {}
    for k in (3, 6):
        hits, misses = 0, []
        for query, state, wanted, _lang in HELD_OUT:
            found = [h["id"] for h in idx.search(query, state=state, k=k)]
            if any(w in found for w in wanted):
                hits += 1
            else:
                misses.append({"query": query, "wanted": wanted, "got": found})
        report[f"hybrid@{k}"] = {"recall": round(hits / len(HELD_OUT), 3), "misses": misses}
        print(f"held-out hybrid recall@{k} = {hits}/{len(HELD_OUT)} = {hits / len(HELD_OUT):.3f}")
    for miss in report["hybrid@6"]["misses"]:
        print(" -", miss["query"], "| wanted", miss["wanted"], "| got", miss["got"][:3])
    out = pathlib.Path(__file__).resolve().parent.parent / "outputs"
    out.mkdir(exist_ok=True)
    (out / "retrieval_heldout.json").write_text(json.dumps({"n_queries": len(HELD_OUT), **report}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
