"""Exercise the conversation loop with a scripted model. No API key needed.

These tests prove the gate's wiring: a hallucinating model cannot get its text to the
user, whatever it does. They do not test how good the real model's advice is. That is
what tests/run_scenarios.py and outputs/ are for.
"""
import datetime as dt
import json
from types import SimpleNamespace as NS

import pytest

from app import agent, documents, triage
from app.tools import ToolBox, add_period

TODAY = dt.date(2026, 11, 7)


class Block(NS):
    def model_dump(self, **_):
        return {k: v for k, v in self.__dict__.items()}


def text(t):
    return Block(type="text", text=t)


def tool(name, args, tid="t1"):
    return Block(type="tool_use", name=name, input=args, id=tid)


def reply(*blocks, stop=None):
    uses_tool = any(b.type == "tool_use" for b in blocks)
    return NS(content=list(blocks), stop_reason=stop or ("tool_use" if uses_tool else "end_turn"), usage=None)


class FakeClient:
    """Plays back scripted replies and records what the app sent."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        self.beta = NS(messages=NS(create=self._create))
        self.messages = NS(create=self._audit)

    def _create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs["messages"], default=str)))
        return self.script.pop(0)

    def _audit(self, **_):
        return reply(text(json.dumps({"claims": [], "uncited_legal_claims": [], "invented_facts": [], "ignored_cautions": []})))


def run(script, message="my landlord kept my deposit", session=None):
    client = FakeClient(script)
    session = session or agent.new_session()
    events = list(agent.run_turn(client, session, message, today=TODAY))
    final = next((e for e in events if e["type"] == "final"), None)
    return final, session, client, events


FAKE = "Under Section 67B of the Rent Act your landlord must pay ten times the deposit."
GOOD = "You can ask for it in writing. Keep your rent receipts."


def test_clean_answer_passes_first_time():
    final, session, client, _ = run([reply(text(GOOD))])
    assert final["text"] == GOOD and final["verification"]["status"] == "verified" and final["verification"]["repairs"] == 0


def test_fake_law_triggers_repair_and_user_never_sees_it():
    final, session, client, _ = run([reply(text(FAKE)), reply(text(GOOD))])
    assert "67B" not in final["text"]
    assert final["verification"]["repairs"] == 1
    # The model was told exactly what was wrong.
    assert "unverified_reference" in json.dumps(client.requests[1], ensure_ascii=False)


def test_persistent_hallucination_is_removed_and_disclosed():
    mixed = f"{GOOD}\n\n{FAKE}"
    final, session, *_ = run([reply(text(mixed))] * 4)
    assert "67B" not in final["text"] and GOOD in final["text"]
    assert final["verification"]["status"] == "verified_with_removals"
    assert "removed 1 part" in final["text"]


def test_nothing_verifiable_means_no_answer_rather_than_a_guess():
    final, session, *_ = run([reply(text(FAKE))] * 4)
    assert "67B" not in final["text"] and final["verification"]["status"] == "blocked"
    assert "15100" in final["text"]


def test_citing_without_reading_is_rejected_then_allowed_after_reading():
    cited = "A cheque case is possible [[NIA1881:s138]]."
    final, session, *_ = run([
        reply(text(cited)),
        reply(tool("get_provision", {"id": "NIA1881:s138"})),
        reply(text(cited)),
    ])
    assert final["text"] == cited and final["verification"]["repairs"] == 1


def test_get_provision_on_invented_section_tells_model_it_does_not_exist():
    final, session, client, _ = run([reply(tool("get_provision", {"id": "TPA1882:s999"})), reply(text(GOOD))])
    tool_result = client.requests[1][-1]["content"][0]
    assert tool_result["is_error"] is True
    assert "No provision with id 'TPA1882:s999' exists" in tool_result["content"]
    assert "TPA1882:s999" not in session["read_ids"]


def test_refusal_is_handled_without_crashing():
    final, *_ = run([reply(stop="refusal")])
    assert final["verification"]["status"] == "refused" and "15100" in final["text"]


def test_audit_rejection_triggers_repair():
    class AuditFails(FakeClient):
        def __init__(self, script):
            super().__init__(script)
            self.audits = 0

        def _audit(self, **_):
            self.audits += 1
            claims = [{"paragraph": 1, "claim": "x", "citations": ["NIA1881:s138"], "verdict": "not_supported", "reason": "text says otherwise"}] if self.audits == 1 else []
            return reply(text(json.dumps({"claims": claims, "uncited_legal_claims": [], "invented_facts": [], "ignored_cautions": []})))

    session = agent.new_session()
    session["read_ids"].append("NIA1881:s138")
    client = AuditFails([reply(text("Banks must pay you double [[NIA1881:s138]].")), reply(text("A bounced cheque can lead to a case [[NIA1881:s138]]."))])
    final = [e for e in agent.run_turn(client, session, "cheque", today=TODAY) if e["type"] == "final"][0]
    assert "double" not in final["text"] and final["verification"]["repairs"] == 1


def test_audit_outage_is_disclosed_not_hidden():
    class AuditDown(FakeClient):
        def _audit(self, **_):
            raise RuntimeError("audit could not finish")

    client = AuditDown([reply(text(GOOD))])
    final = [e for e in agent.run_turn(client, agent.new_session(), "hello", today=TODAY) if e["type"] == "final"][0]
    assert final["verification"]["status"] == "citations_checked_only"


def test_history_keeps_assistant_blocks_unchanged():
    thinking = Block(type="thinking", thinking="", signature="sig123")
    final, session, *_ = run([reply(thinking, text(GOOD))])
    assistant = [m for m in session["messages"] if m["role"] == "assistant"][0]
    assert assistant["content"][0]["signature"] == "sig123"


# ---- Triage: emergencies do not depend on the model ----

@pytest.mark.parametrize("message,level", [
    ("my husband beats me every night", "emergency"),
    ("मेरा पति मुझे पीटता है", "emergency"),
    ("I want to end my life over this debt", "emergency"),
    ("police came and arrested my brother", "urgent"),
    ("मुझे कोर्ट से समन आया है", "urgent"),
    ("landlord changed the locks and threw me out", "urgent"),
    ("Got a cheque bounce notice from a lawyer yesterday", "urgent"),
    ("This is an offence under Section 138 of the Negotiable Instruments Act", "urgent"),
    ("failing which my client will initiate criminal proceedings against you", "urgent"),
    ("I want a divorce", "recommended"),
    ("a dispute over ancestral property worth 3 crores", "recommended"),
    ("my landlord has not returned my deposit", "none"),
    ("flipkart did not refund me", "none"),
    ("my salary has been late for two months", "none"),
])
def test_triage_levels(message, level):
    assert triage.assess(message)["level"] == level


def test_model_cannot_lower_the_triage_level():
    session = agent.new_session()
    session["triage"] = {"level": "urgent", "codes": [], "reasons": [], "help": []}
    box = ToolBox(session, today=TODAY)
    box.run("set_assessment", {"lawyer_needed": "none", "reason": "fine", "confidence": "high", "what_i_could_not_verify": []})
    assert session["assessment"]["lawyer_needed"] == "urgent"


def test_emergency_help_reaches_the_person_with_verified_numbers():
    _, _, _, events = run([reply(text("Please call now [[NOTE:EMERGENCY]]."))], message="my husband beats me")
    event = next(e for e in events if e["type"] == "triage")
    assert event["triage"]["level"] == "emergency"
    assert "112" in json.dumps(event["triage"]["help_notes"])


# ---- Tools ----

def test_date_calc_handles_month_ends_and_leap_years():
    assert add_period(dt.date(2026, 1, 31), 1, "months") == dt.date(2026, 2, 28)
    assert add_period(dt.date(2024, 2, 29), 1, "years") == dt.date(2025, 2, 28)
    assert add_period(dt.date(2023, 3, 15), 3, "years") == dt.date(2026, 3, 15)


def test_document_with_invented_facts_is_not_saved():
    session = agent.new_session()
    session["user_text"] = "I paid a deposit of Rs 40000"
    box = ToolBox(session, today=TODAY)
    result, is_error = box.run("save_document", {"doc_type": "legal_notice", "title": "Notice", "fill_in": [],
                                                 "body": "You took Rs. 75,000 on 3 March 2024."})
    assert is_error and "NOT SAVED" in result and not session["documents"]


def test_document_with_user_facts_and_placeholders_is_saved_and_exports():
    session = agent.new_session()
    session["user_text"] = "I paid a deposit of Rs 40000"
    session["read_ids"] = ["ICA1872:s73"]
    box = ToolBox(session, today=TODAY)
    _, is_error = box.run("save_document", {"doc_type": "legal_notice", "title": "Legal Notice", "fill_in": ["Your address"],
                                            "body": "# Notice\n\nTo [LANDLORD NAME].\n\nI paid Rs. 40,000 as deposit. Damages may follow a breach [[ICA1872:s73]]."})
    assert not is_error
    data = documents.to_docx(session["documents"][0])
    assert data[:2] == b"PK"


def test_upload_lets_a_notice_be_discussed_but_chat_alone_cannot_launder_a_reference():
    session = agent.new_session()
    box = ToolBox(session, today=TODAY)
    args = dict.fromkeys(["problem_type", "state", "summary", "parties", "timeline", "amounts", "evidence", "deadlines", "open_questions"])
    box.run("update_case_file", {**args, "document_references": ["Section 420 IPC"]})
    assert "420" not in session["user_text"]
    session["has_upload"] = True
    box.run("update_case_file", {**args, "document_references": ["Section 420 IPC"]})
    assert "420" in session["user_text"]


def test_practice_opponent_cannot_cite_law():
    session = agent.new_session()
    agent.practice_start(session, "landlord", "bluffing")
    client = FakeClient([reply(text("Section 12 of the Rent Act says I keep it.")), reply(text("Section 12 still says so."))])
    answer = agent.practice_turn(client, session, "Return my deposit")
    assert "Section" not in answer


def test_session_survives_a_save_and_reload_through_json():
    final, session, *_ = run([reply(Block(type="thinking", thinking="", signature="s"), text(GOOD))])
    reloaded = json.loads(json.dumps(session, ensure_ascii=False))
    assert reloaded["messages"][-1]["content"][0]["signature"] == "s"
    assert reloaded["log"][0]["final"] == GOOD


# ---- Fixes from reading the first real transcript ----

ENGLISH = "My landlord in Chennai has kept my deposit for three months now. What can I do about it?"
HINDI_REPLY = "आप एक लिखित मांग भेज सकते हैं और अपनी रसीदें संभाल कर रखें। इससे आपका पक्ष मजबूत होगा और आगे मदद मिलेगी।"
ENGLISH_REPLY = "You can send a written demand and keep your receipts safe. That makes your position stronger."


def test_english_question_answered_in_hindi_is_sent_back():
    final, _, client, _ = run([reply(text(HINDI_REPLY)), reply(text(ENGLISH_REPLY))], message=ENGLISH)
    assert final["text"] == ENGLISH_REPLY and "wrong_language" in json.dumps(client.requests[1], ensure_ascii=False)


def test_stubborn_wrong_language_still_answers_rather_than_refusing():
    final, *_ = run([reply(text(HINDI_REPLY))] * 4, message=ENGLISH)
    assert final["text"] == HINDI_REPLY


def test_person_asking_for_hindi_is_respected():
    final, *_ = run([reply(text(HINDI_REPLY))], message="Please explain my deposit rights in Hindi, I find it easier to read.")
    assert final["verification"]["repairs"] == 0


def test_hinglish_is_not_treated_as_wrong_language():
    final, *_ = run([reply(text("Aap landlord ko likhit mein demand bhejiye aur apni receipts sambhal ke rakhiye."))],
                    message="mera landlord deposit wapas nahi de raha hai kya karun")
    assert final["verification"]["repairs"] == 0


def _auditing_client(script, verdicts):
    class C(FakeClient):
        def _audit(self, **_):
            return reply(text(json.dumps(verdicts.pop(0))))
    return C(script)


EMPTY = {"claims": [], "uncited_legal_claims": [], "invented_facts": [], "ignored_cautions": []}


def test_text_left_after_removals_still_gets_the_claim_check():
    uncited = "You can complain to the Rent Court in Chennai."
    draft = f"{GOOD}\n\n{uncited}\n\n{FAKE}"
    audit = {**EMPTY, "uncited_legal_claims": [{"paragraph": 2, "claim": uncited}]}
    client = _auditing_client([reply(text(draft))] * 4, [audit])
    final = [e for e in agent.run_turn(client, agent.new_session(), "deposit", today=TODAY) if e["type"] == "final"][0]
    assert "Rent Court" not in final["text"] and "67B" not in final["text"] and GOOD in final["text"]
    assert final["verification"]["status"] == "verified_with_removals" and final["verification"]["removed"] == 2


def test_document_with_a_fact_the_person_never_gave_is_rejected_then_fixed():
    args = {"doc_type": "demand_letter", "title": "Letter", "fill_in": [], "body": "There are no outstanding dues."}
    fixed = {**args, "body": "Dues, if any: [DETAILS OF ANY DUES]."}
    audit = {**EMPTY, "invented_facts": [{"paragraph": 1, "fact": "no outstanding dues"}]}
    client = _auditing_client([reply(tool("save_document", args)), reply(tool("save_document", fixed, "t2")), reply(text(GOOD))], [audit, EMPTY, EMPTY])
    session = agent.new_session()
    list(agent.run_turn(client, session, "please draft a letter", today=TODAY))
    assert [d["body"] for d in session["documents"]] == [fixed["body"]]
    assert "invented_fact" in json.dumps(client.requests[1])
    assert session["documents"][0]["audit"] == "passed"


def test_todays_date_on_a_letter_is_not_an_invented_fact():
    args = {"doc_type": "demand_letter", "title": "Letter", "fill_in": [], "body": "Date: 7 November 2026"}
    client = FakeClient([reply(tool("save_document", args)), reply(text(GOOD))])
    session = agent.new_session()
    list(agent.run_turn(client, session, "please draft a letter", today=TODAY))
    assert len(session["documents"]) == 1


# ---- Fix from the first DeepSeek run: the advice arrived alongside tool calls ----

LONG_ADVICE = ("In Tamil Nadu the law gives you a clear starting point on this. The deposit must be refunded within one month after "
               "you vacate, after deducting anything you actually owe [[TNRRA2017:s11]]. Keep your bank proof safe, and send a written demand.")


def test_advice_written_alongside_a_tool_call_reaches_the_person_and_gets_checked():
    final, *_ = run([
        reply(text(LONG_ADVICE), tool("get_provision", {"id": "TNRRA2017:s11"})),
        reply(text("Answer me on those points whenever you are ready.")),
    ])
    assert "one month after" in final["text"] and "whenever you are ready" in final["text"]
    assert [c["id"] for c in final["citations"]] == ["TNRRA2017:s11"]


def test_invented_law_written_alongside_a_tool_call_is_still_stopped():
    fake = FAKE + " " + "This is a long paragraph so that it counts as part of the answer rather than a passing remark, and it must be checked. " * 2
    final, *_ = run([
        reply(text(fake), tool("get_provision", {"id": "TNRRA2017:s11"})),
        reply(text("Tell me when you are ready.")),
        reply(text(GOOD)),
    ])
    assert "67B" not in final["text"] and final["text"] == GOOD


def test_short_remarks_before_a_tool_call_are_left_out():
    final, *_ = run([reply(text("Let me check the law."), tool("get_provision", {"id": "TNRRA2017:s11"})), reply(text(GOOD))])
    assert final["text"] == GOOD


# ---- Fixes from the second DeepSeek run ----

@pytest.mark.parametrize("written,expected", [
    ("Tamil Nadu (Chennai)", "tamil nadu"), ("Chennai", "tamil nadu"), ("Koramangala, Bangalore", "karnataka"),
    ("Karnataka", "karnataka"), ("New Delhi", "delhi"), ("Pune, Maharashtra", "maharashtra"), ("Uttar Pradesh", "uttar pradesh"),
    ("Unknown", None), ("not stated", None), ("India", None),
])
def test_state_is_understood_however_the_model_writes_it(written, expected):
    from app.retrieval import normalise_state
    assert normalise_state(written) == expected


def test_a_states_own_law_is_never_flagged_as_the_wrong_state():
    from app.verifier import verify
    report = verify("The deposit comes back within one month [[TNRRA2017:s11]].", {"TNRRA2017:s11"}, state="Tamil Nadu (Chennai)")
    assert report.ok


def test_removal_leaves_no_empty_headings():
    from app.verifier import Issue, strip_failed_paragraphs
    text = "**Intro**\n\nGood advice.\n\n**What the law says**\n\nBad claim.\n\n**Next steps**\n\nMore advice.\n\n**Last**\n\nAnother bad claim."
    cleaned, removed = strip_failed_paragraphs(text, [Issue("x", "y", 3), Issue("x", "y", 7)])
    assert removed == 2 and "What the law says" not in cleaned and "**Last**" not in cleaned
    assert "**Intro**" in cleaned and "**Next steps**" in cleaned


def test_auditor_sees_the_date_tools_calculations():
    from app import audit
    client = FakeClient([])
    seen = {}
    original = client.messages.create

    def spy(**kwargs):
        seen.update(kwargs)
        return reply(text(json.dumps(EMPTY)))

    client.messages = NS(create=spy)
    audit.audit_answer(client, "That was 68 days ago.", "I left on 31 July", "68 days")
    assert "68 days" in seen["messages"][0]["content"].split("<sources>")[0].split("<app_calculations>")[1]


def test_source_copy_note_is_not_a_caution_the_reply_must_repeat():
    from app.corpus import library
    assert "third-party" not in library().caution("TNRRA2017:s11")
    assert library().public_record("TNRRA2017:s11")["mirror"] is True


# ---- Fixes from reading all 27 transcripts ----

def test_only_the_flagged_sentence_is_removed():
    from app.verifier import Issue, strip_failed_paragraphs
    text = "The deposit comes back within one month [[TNRRA2017:s11]]. Only the Rent Court can hear this. Keep your bank proof."
    cleaned, removed = strip_failed_paragraphs(text, [Issue("claim_overstated", "x", 0, quote="Only the Rent Court can hear this.")])
    assert removed == 1 and "Rent Court" not in cleaned and "within one month" in cleaned and "bank proof" in cleaned


def test_sentence_removal_works_in_hindi_and_keeps_list_lines():
    from app.verifier import Issue, strip_failed_paragraphs
    text = "**क्या करें**\n- पहला कदम लिखित मांग है।\n- अदालत हमेशा आपके पक्ष में फैसला देगी।\n- रसीदें संभाल कर रखें।"
    cleaned, _ = strip_failed_paragraphs(text, [Issue("x", "y", 0, quote="अदालत हमेशा आपके पक्ष में फैसला देगी।")])
    assert cleaned == "**क्या करें**\n- पहला कदम लिखित मांग है।\n- रसीदें संभाल कर रखें।"


def test_unfindable_quote_falls_back_to_removing_the_paragraph():
    from app.verifier import Issue, strip_failed_paragraphs
    text = "Keep this paragraph.\n\nBad paragraph here."
    cleaned, removed = strip_failed_paragraphs(text, [Issue("x", "y", 1, quote="A sentence that the draft never contained.")])
    assert cleaned == "Keep this paragraph." and removed == 1


def test_invented_law_is_still_removed_whole_when_there_is_no_quote():
    from app.verifier import strip_failed_paragraphs, verify
    text = f"{GOOD}\n\n{FAKE}"
    cleaned, _ = strip_failed_paragraphs(text, verify(text, set()).issues)
    assert "67B" not in cleaned and GOOD in cleaned


def test_everyday_ways_of_stating_amounts_and_periods_are_understood():
    from app.verifier import durations, fact_check_document, money
    assert 80_000 in money("80000 rupaye deposit tha")
    assert 80_000 in money("80,000/- only")
    assert 18_000 in money("মাসে ১৮,০০০ টাকা")
    assert (11, "month") in durations("11 mahine ka agreement")
    assert (2, "month") in durations("do mahine ho gaye")
    assert (3, "month") in durations("teen mahine se salary nahi")
    assert not fact_check_document("You hold my deposit of Rs. 80,000.", "80000 rupaye deposit tha")


# ---- Free-tier runner ----

def _free_setup(monkeypatch, tmp_path):
    from app import config
    from tests import run_scenarios as rs
    monkeypatch.setattr(config, "PROVIDER", "gemini")
    monkeypatch.setattr(config, "MODEL", config.MODEL)
    monkeypatch.setattr(config, "AUDIT_MODEL", config.AUDIT_MODEL)
    monkeypatch.setattr(rs, "RAW", tmp_path)
    return rs, config


def test_free_run_moves_to_next_model_and_never_saves_a_provider_failure(monkeypatch, tmp_path):
    rs, config = _free_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(rs, "FREE_MODELS", ["a", "b"])
    monkeypatch.setattr(rs, "FREE_AUDIT_MODELS", ["x"])
    used = []

    def fake(scenario):
        used.append(config.MODEL)
        error = "API 429: per day" if config.MODEL == "a" else None
        return {"id": scenario["id"], "provider": "gemini", "trail": [{"error": error}], "checks": [], "seconds": 1, "model": config.MODEL}

    monkeypatch.setattr(rs, "FREE_PRIORITY", ["18", "19"])
    saved = rs.free_run(fake)
    assert used == ["a", "b", "b"] and len(saved) == 2
    assert all(json.loads(f.read_text())["model"] == "b" for f in tmp_path.glob("*.json"))


def test_free_run_stops_cleanly_when_all_quota_is_gone_and_later_resumes(monkeypatch, tmp_path):
    rs, config = _free_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(rs, "FREE_MODELS", ["a"])
    monkeypatch.setattr(rs, "FREE_AUDIT_MODELS", ["x"])
    monkeypatch.setattr(rs, "FREE_PRIORITY", ["18", "19"])
    calls = {"n": 0}

    def fake(scenario):
        calls["n"] += 1
        error = "API 429" if calls["n"] == 2 else None
        return {"id": scenario["id"], "provider": "gemini", "trail": [{"error": error}], "checks": [], "seconds": 1, "model": config.MODEL}

    assert len(rs.free_run(fake)) == 1
    assert len(rs.free_run(fake)) == 1  # The next day it only does the one still missing.
    assert len(list(tmp_path.glob("*.json"))) == 2


def test_audit_quota_failure_drops_the_auditor_not_the_writer(monkeypatch, tmp_path):
    rs, config = _free_setup(monkeypatch, tmp_path)
    monkeypatch.setattr(rs, "FREE_MODELS", ["a"])
    monkeypatch.setattr(rs, "FREE_AUDIT_MODELS", ["x", "y"])
    monkeypatch.setattr(rs, "FREE_PRIORITY", ["18"])

    def fake(scenario):
        error = "429" if config.AUDIT_MODEL == "x" else None
        return {"id": scenario["id"], "provider": "gemini", "trail": [{"audit_error": error}], "checks": [], "seconds": 1, "audit_model": config.AUDIT_MODEL}

    rs.free_run(fake)
    assert json.loads(next(tmp_path.glob("*.json")).read_text())["audit_model"] == "y"


# ---- Fixes from reading the Lite-model transcripts ----

@pytest.mark.parametrize("text,code", [
    ("First, to put your mind at rest: You will not be arrested.", "uncited_law_statement"),
    ("No one can arrest you over unpaid maintenance.", "uncited_law_statement"),
    ("Under general contract and property principles, a security deposit is refundable.", "uncited_law_statement"),
    ("भारतीय कानून के तहत मकान मालिक को डिपॉजिट लौटाना होता है।", "uncited_law_statement"),
    ("That law does not exist. It is completely fabricated.", "claimed_nonexistence"),
    ("There is no Section 67B in the rent laws.", "claimed_nonexistence"),
    ("Please speak to a lawyer about the Hindu Marriage Act.", "law_not_in_library"),
])
def test_unsupported_statements_are_stopped_without_needing_the_audit(text, code):
    from app.verifier import verify
    assert code in {i.code for i in verify(text, set()).issues}


@pytest.mark.parametrize("text", [
    "I could not find that section in the laws I can verify.",
    "Whether you can be arrested is something I cannot verify, so please ask a lawyer.",
    "Family law is outside what I can verify, including the Hindu Marriage Act.",
    "Under the law, the deposit must come back within one month [[TNRRA2017:s11]].",
    "Keep your receipts and send a written demand.",
    "I cannot help you make fake receipts.",
])
def test_honest_or_sourced_statements_still_pass(text):
    from app.verifier import verify
    report = verify(text, {"TNRRA2017:s11"})
    assert report.ok, report.feedback()


def test_english_question_answered_in_hinglish_is_sent_back():
    from app.verifier import language_mismatch
    question = "I rent a flat in Lucknow and my landlord will not give back my deposit. What does the law say?"
    assert "Hinglish" in language_mismatch(question, "Aapke paas agreement hai toh aap landlord ko notice bhej sakte hain, lekin pehle mujhe yeh batayein ki kya hua.")
    assert language_mismatch(question, "You can send your landlord a written demand and keep your receipts safe.") is None


def test_stray_flag_emoji_is_removed():
    final, *_ = run([reply(text("\U0001F1E6\U0001F1EA\U0001F1EE\U0001F1F3\n\n" + GOOD))])
    assert final["text"] == GOOD
