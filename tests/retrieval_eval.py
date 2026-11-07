"""Measure search quality: does the right law show up for how people really talk?

Run: .venv/bin/python tests/retrieval_eval.py
Each query lists acceptable provisions. A hit means any of them is in the top K.
The queries were written from everyday problems before looking at search results.
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from app.retrieval import index  # noqa: E402

GOLD = [
    # (query, state, acceptable ids, language)
    ("landlord not returning security deposit after I vacated", "Tamil Nadu", ["TNRRA2017:s11"], "en"),
    ("how much security deposit can a landlord charge", "Tamil Nadu", ["TNRRA2017:s11"], "en"),
    ("model tenancy security deposit limit", None, ["MTA2021:s11"], "en"),
    ("mera makan malik deposit wapas nahi kar raha", "Tamil Nadu", ["TNRRA2017:s11"], "hinglish"),
    ("मकान मालिक सिक्योरिटी डिपॉजिट वापस नहीं कर रहा", "Tamil Nadu", ["TNRRA2017:s11"], "hi"),
    ("notice period to end a lease of a house", None, ["TPA1882:s106"], "en"),
    ("what is a lease", None, ["TPA1882:s105"], "en"),
    ("compensation for breach of contract", None, ["ICA1872:s73"], "en"),
    ("time limit to sue for breach of contract", None, ["LIM1963:art55"], "en"),
    ("how long do I have to file a civil suit to recover money", None, ["LIM1963:art55", "LIM1963:art113", "LIM1963:s3", "LIM1963:art19"], "en"),
    ("company did not refund my money for defective product", None, ["CPA2019:s2", "CPA2019:s35", "CPA2019:s39", "CPECOM2020:r7", "CPECOM2020:r6", "CPECOM2020:r4"], "en"),
    ("online shopping site refuses refund", None, ["CPECOM2020:r4", "CPECOM2020:r5", "CPECOM2020:r6", "CPECOM2020:r7"], "en"),
    ("where to file a consumer complaint and how", None, ["CPA2019:s35", "CPA2019:s34"], "en"),
    ("time limit for consumer complaint", None, ["CPA2019:s69"], "en"),
    ("district commission money limit value of goods", None, ["CPJUR2021:r3", "CPA2019:s34"], "en"),
    ("what remedies can the consumer commission order", None, ["CPA2019:s39"], "en"),
    ("e-commerce grievance officer must respond within", None, ["CPECOM2020:r4", "CPECOM2020:r6"], "en"),
    ("what counts as deficiency in service", None, ["CPA2019:s2"], "en"),
    ("employer has not paid my salary for three months", None, ["COW2019:s17", "COW2019:s45", "COW2019:s18"], "en"),
    ("by when must wages be paid", None, ["COW2019:s17"], "en"),
    ("final salary after resignation or dismissal paid within", None, ["COW2019:s17"], "en"),
    ("where do I claim unpaid wages and time limit", None, ["COW2019:s45"], "en"),
    ("salary nahi mila 3 mahine se company nahi de rahi", None, ["COW2019:s17", "COW2019:s45"], "hinglish"),
    ("तीन महीने से वेतन नहीं मिला", None, ["COW2019:s17", "COW2019:s45"], "hi"),
    ("deductions from salary allowed", None, ["COW2019:s18"], "en"),
    ("gratuity payable after five years", None, ["SSC2020:s53"], "en"),
    ("retrenchment compensation worker", None, ["IRC2020:s70", "IRC2020:s67", "IRC2020:s2"], "en"),
    ("cheque bounced insufficient funds", None, ["NIA1881:s138"], "en"),
    ("cheque bounce notice 15 days to pay", None, ["NIA1881:s138"], "en"),
    ("time limit to file a complaint for dishonoured cheque", None, ["NIA1881:s142"], "en"),
    ("चेक बाउंस हो गया", None, ["NIA1881:s138"], "hi"),
    ("who gets free legal aid", None, ["LSA1987:s12"], "en"),
    ("muft vakil kaun le sakta hai", None, ["LSA1987:s12"], "hinglish"),
    ("मुफ्त कानूनी सहायता किसे मिलेगी", None, ["LSA1987:s12"], "hi"),
    ("lok adalat settlement", None, ["LSA1987:s19", "LSA1987:s20", "LSA1987:s21", "LSA1987:s22B", "LSA1987:s22C"], "en"),
    ("cheating someone out of money punishment", None, ["BNS2023:s318"], "en"),
    ("criminal breach of trust", None, ["BNS2023:s316"], "en"),
    ("criminal intimidation threats", None, ["BNS2023:s351"], "en"),
    ("how to file an FIR police information cognizable", None, ["BNSS2023:s173"], "en"),
    ("WhatsApp messages screenshots as evidence in court", None, ["BSA2023:s63", "BSA2023:s61"], "en"),
    ("summary suit to recover money on a written contract", None, ["CPC1908:o37"], "en"),
    ("is the Indian Penal Code still in force", None, ["BNS2023:s358"], "en"),
    ("Payment of Wages Act repealed", None, ["COW2019:s69"], "en"),
    ("Karnataka tenant landlord rent", "Karnataka", ["KRA1999:s3", "KRA1999:s4", "KRA1999:s5", "KRA1999:s27", "KRA1999:s2"], "en"),
    ("eviction of tenant by landlord grounds Delhi", "Delhi", ["DRC1958:s14"], "en"),
    ("eviction grounds Maharashtra", "Maharashtra", ["MRC1999:s16"], "en"),
]


def evaluate(mode, k):
    hits, misses = 0, []
    idx = index()
    per_lang = {}
    for query, state, wanted, lang in GOLD:
        found = [h["id"] for h in idx.search(query, state=state, k=k, mode=mode)]
        ok = any(w in found for w in wanted)
        hits += ok
        rec = per_lang.setdefault(lang, [0, 0])
        rec[0] += ok
        rec[1] += 1
        if not ok:
            misses.append({"query": query, "wanted": wanted, "got": found[:k]})
    return hits / len(GOLD), per_lang, misses


def main():
    results = {}
    for mode in ("bm25", "dense", "hybrid"):
        for k in (3, 6):
            recall, per_lang, misses = evaluate(mode, k)
            results[f"{mode}@{k}"] = {"recall": round(recall, 3), "by_language": per_lang, "misses": misses}
            print(f"{mode:7} recall@{k} = {recall:.3f}   {per_lang}")
    print("\nHybrid misses at k=6:")
    for miss in results["hybrid@6"]["misses"]:
        print(" -", miss["query"], "| wanted", miss["wanted"], "| got", miss["got"][:4])
    out = pathlib.Path(__file__).resolve().parent.parent / "outputs"
    out.mkdir(exist_ok=True)
    (out / "retrieval_eval.json").write_text(json.dumps({"n_queries": len(GOLD), "results": results}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
