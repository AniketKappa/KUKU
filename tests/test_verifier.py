"""Red-team the verifier with the exact mistakes language models make.

No API key needed. Each test hands the gate a hallucinated or careless answer and asserts
that it gets stopped, then hands it a correct answer and asserts that it gets through.
A verifier that blocked everything would pass half these tests and fail the other half.
"""
import pytest

from app.corpus import library
from app.verifier import durations, fact_check_document, money, strip_failed_paragraphs, verify

READ = {"CPA2019:s35", "CPA2019:s34", "CPJUR2021:r3", "NIA1881:s138", "COW2019:s17", "COW2019:s45", "COW2019:s69", "TNRRA2017:s11",
        "BNS2023:s318", "BNS2023:s358", "LIM1963:art55", "NOTE:NCH", "KRA1999:s33", "MTA2021:s11", "ICA1872:s239", "CPA2019:s69"}


def codes(report):
    return {i.code for i in report.issues if i.blocking}


# ---- Things that must be blocked ----

@pytest.mark.parametrize("name,text,expected", [
    ("invented section id", "Your landlord must pay you ten times the deposit [[TPA1882:s999]].", "citation_not_in_library"),
    ("invented act id", "This is covered by law [[RENT2020:s12]].", "citation_not_in_library"),
    ("cited but never read", "Damages are available [[ICA1872:s73]].", "citation_not_read"),
    ("bare invented section", "Under Section 67B of the Rent Act you get ten times the deposit.", "unverified_reference"),
    ("real number, no citation", "Under section 420 this is an offence.", "unverified_reference"),
    ("hindi invented section", "धारा 420 के तहत मकान मालिक पर केस होगा।", "unverified_reference"),
    ("number before word in hindi", "420 धारा के तहत केस होगा।", "unverified_reference"),
    ("marathi word for section", "कलम 299 नुसार दावा करा.", "unverified_reference"),
    ("tamil word for section", "பிரிவு 299 இன் கீழ் புகார் செய்யலாம்.", "unverified_reference"),
    ("list of sections", "Sections 40, 41 and 42 protect you.", "unverified_reference"),
    ("section in other paragraph", "See [[CPA2019:s35]].\n\nAlso, section 35 helps you.", "reference_without_citation"),
    ("invented order", "File under Order XXXVII of the CPC.", "unverified_reference"),
    ("AIR citation", "See AIR 2019 SC 1234 on this point.", "case_law_citation"),
    ("SCC citation", "See (2019) 3 SCC 123 on this point.", "case_law_citation"),
    ("versus case", "In Sharma v. Gupta the deposit was refunded.", "case_law_citation"),
    ("hindi versus", "शर्मा बनाम गुप्ता मामले में जमा राशि लौटाई गई।", "case_law_citation"),
    ("court held, no cite", "The Supreme Court has held that deposits must be refunded.", "case_law_citation"),
    ("hindi court held", "सुप्रीम कोर्ट ने अपने फैसले में कहा है कि जमा लौटाना होगा।", "case_law_citation"),
    ("law outside library", "The Real Estate Regulation Act, 2016 applies here.", "law_not_in_library"),
    ("repealed IPC", "This is cheating under IPC.", "repealed_law"),
    ("repealed wages act", "You can claim under the Payment of Wages Act, 1936.", "repealed_law"),
    ("repealed hindi", "आईपीसी के तहत यह अपराध है।", "repealed_law"),
    ("repealed consumer act", "File under the Consumer Protection Act, 1986.", "repealed_law"),
    ("repealed provision", "This rule applies [[ICA1872:s239]].", "citation_repealed_provision"),
    ("wrong duration", "You must file within 90 days [[CPA2019:s35]].", "unsupported_number"),
    ("wrong duration word", "You have five years to file [[CPA2019:s69]].", "unsupported_number"),
    ("hindi duration", "आपके पास 90 दिन हैं [[CPA2019:s35]].", "unsupported_number"),
    ("hindi word duration", "आपके पास तीन साल हैं [[CPA2019:s35]].", "unsupported_number"),
    ("uncited duration", "You must send a notice within 45 days.", "unsupported_number"),
    ("outdated limit from the 2019 text", "District Commissions hear claims up to rupees 75 lakh [[CPJUR2021:r3]].", "unsupported_number"),
    ("fake quote", 'The Act says "every landlord shall refund the deposit with double interest" [[TNRRA2017:s11]].', "misquote"),
])
def test_blocks_hallucination(name, text, expected):
    state = None
    assert expected in codes(verify(text, READ, user_text="", state=state)), name


def test_blocks_other_states_law():
    report = verify("You can give notice of the sub-tenancy [[KRA1999:s33]].", READ, state="Delhi")
    assert "wrong_state_law" in codes(report)


def test_state_alias_resolves_city():
    report = verify("You can give notice of the sub-tenancy [[KRA1999:s33]].", READ, state="Bengaluru")
    assert "wrong_state_law" not in codes(report)


# ---- Things that must be allowed: a gate that blocks everything is useless ----

@pytest.mark.parametrize("name,text,user", [
    ("correct citation", "A cheque that bounces can lead to a case [[NIA1881:s138]].", ""),
    ("correct section words", "Section 138 covers bounced cheques [[NIA1881:s138]].", ""),
    ("hindi correct", "धारा 138 के तहत चेक बाउंस का मामला बनता है [[NIA1881:s138]]।", ""),
    ("duration from source, words vs digits", "You have 15 days to pay after the notice [[NIA1881:s138]].", ""),
    ("duration in source as words", "Pay within fifteen days of the notice [[NIA1881:s138]].", ""),
    ("hindi duration from source", "नोटिस मिलने के बाद 15 दिन में भुगतान करना होगा [[NIA1881:s138]]।", ""),
    ("money from rules", "District Commissions hear claims up to 50 lakh rupees [[CPJUR2021:r3]].", ""),
    ("user's own numbers", "You paid a deposit of Rs. 40,000 and moved out 3 months ago.", "I paid Rs 40,000 deposit, left 3 months back"),
    ("user's own section", "Your notice mentions section 420. I could not verify that part.", "the notice says section 420"),
    ("repealed law with successor", "The IPC was replaced. Cheating is now covered here [[BNS2023:s318]].", ""),
    ("repeal stated with proof", "The Payment of Wages Act no longer applies [[COW2019:s69]].", ""),
    ("no judgments disclaimer", "I do not cite court judgments, because I cannot verify them.", ""),
    ("hindi disclaimer", "मैं अदालत के फैसले का हवाला नहीं देता क्योंकि उनकी पुष्टि नहीं कर सकता।", ""),
    ("known act name", "The Consumer Protection Act, 2019 applies [[CPA2019:s35]].", ""),
    ("exact quote survives odd spacing", 'The law says "security deposit shall be refunded to the tenant within one month" [[TNRRA2017:s11]].', ""),
    ("helpline note", "You can call 1915 [[NOTE:NCH]].", ""),
    ("plain advice", "Keep your rent receipts and bank statements safe.", ""),
])
def test_allows_correct_answers(name, text, user):
    report = verify(text, READ, user_text=user)
    assert report.ok, f"{name}: {report.feedback()}"


def test_user_can_ask_about_a_law_they_named():
    report = verify("The Real Estate Regulation Act, 2016 you mention is not in my library, so I cannot verify it.",
                    READ, user_text="what about Real Estate Regulation Act, 2016")
    assert "law_not_in_library" not in codes(report)


def test_tool_output_is_a_valid_source_of_numbers():
    report = verify("That leaves you 12 days.", READ, tool_numbers="12 days")
    assert report.ok


# ---- Document guard: nothing false may be sent in the user's name ----

def test_document_rejects_invented_date_and_amount():
    issues = fact_check_document("You took Rs. 75,000 on 3 March 2024 from me.", "I paid 40000 in 2023")
    details = " ".join(i.detail for i in issues)
    assert "3 March 2024" in details and "75,000" in details


def test_document_accepts_user_facts_in_other_formats():
    assert not fact_check_document("You took Rs. 40,000 on 03/03/2024.", "I paid Rs 40000 on 3 March 2024")


def test_document_allows_placeholders():
    assert not fact_check_document("Dated [DATE OF NOTICE]. You owe [AMOUNT].", "")


# ---- Last-resort removal ----

def test_strip_removes_only_failed_paragraphs():
    text = "Good advice here.\n\nUnder Section 67B you get ten times the deposit.\n\nMore good advice."
    report = verify(text, READ)
    cleaned, removed = strip_failed_paragraphs(text, report.issues)
    assert removed == 1 and "67B" not in cleaned and "More good advice" in cleaned


# ---- Number parsing ----

def test_duration_and_money_parsing():
    assert (15, "day") in durations("within fifteen days")
    assert (15, "day") in durations("१५ दिन") or (15, "day") in durations("15 दिन")
    assert (3, "year") in durations("तीन साल")
    assert (6, "month") in durations("six months")
    assert 5_000_000 in money("fifty lakh rupees")
    assert 5_000_000 in money("₹50 lakh")
    assert 5_000_000 in money("Rs. 50,00,000")
    assert 40_000 in money("₹40,000")
    assert 20_000_000 in money("2 crore रुपये") or 20_000_000 in money("₹2 करोड़")


# ---- Library integrity ----

def test_every_repeal_is_proven_by_the_replacing_act():
    lib = library()
    for law in lib.repealed_laws:
        text = " ".join(lib.records[law["proof"]]["text"].split()).lower()
        short = law["name"].split(",")[0].lower()
        assert short in text, f"{law['proof']} does not mention {short}"


def test_every_caution_points_at_a_real_provision():
    lib = library()
    for pid in lib.cautions:
        assert pid in lib.records
    for sid in lib.source_cautions:
        assert sid in lib.sources
    for rule in ("CPJUR2021:r3", "CPJUR2021:r4", "CPJUR2021:r5"):
        assert rule in lib.records


def test_only_verified_notes_are_served():
    notes = library().notes
    assert notes and all(n["verified"] for n in notes.values())


def test_every_topic_pointer_resolves_to_a_real_provision():
    import json
    from app import config
    lib = library()
    for topic in json.loads((config.CORPUS / "topics.json").read_text())["topics"]:
        for pid in topic["provisions"]:
            assert lib.get(pid), f"{topic['name']} points at missing {pid}"
