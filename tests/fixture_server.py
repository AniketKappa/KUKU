"""Run the real app with a SCRIPTED model, to check the screens without an API key.

The screen says it's a scripted demo. The verifier, library, tools, triage and document
export are all the real ones. Only the model's replies are pre-written. This never
produces evidence of how good the real model's advice is.

Run: .venv/bin/python -m uvicorn tests.fixture_server:app --port 8765
"""
import json
from types import SimpleNamespace as NS

from app import agent, config, server

app = server.app


class Block(NS):
    def model_dump(self, **_):
        return dict(self.__dict__)


def _text(t):
    return Block(type="text", text=t)


def _tool(name, args, tid):
    return Block(type="tool_use", name=name, input=args, id=tid)


def _reply(*blocks):
    stop = "tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn"
    return NS(content=list(blocks), stop_reason=stop, usage=None)


CASE = {
    "problem_type": "tenancy_deposit", "state": "Tamil Nadu",
    "summary": "The tenant left a rented flat in Chennai. The landlord has not returned the deposit of Rs 40,000.",
    "parties": [{"role": "landlord", "name": "unknown"}],
    "timeline": [{"date": "about 3 months ago", "event": "Moved out of the flat", "source": "user"},
                 {"date": "unknown", "event": "Asked for the deposit back; no reply", "source": "user"}],
    "amounts": [{"label": "Security deposit", "amount_inr": "₹40,000"}],
    "evidence": [{"item": "Rent agreement", "status": "unclear", "why_it_matters": "Shows the deposit and its terms"},
                 {"item": "Bank proof of the deposit", "status": "have", "why_it_matters": "Proves you paid it"},
                 {"item": "Written demand to the landlord", "status": "missing", "why_it_matters": "Shows you asked and they refused"}],
    "deadlines": None, "open_questions": ["Did you hand over the keys on a known date?"], "document_references": None,
}
ANSWER = ("In Tamil Nadu, the law gives you a clear starting point. The deposit must be refunded within one month after you vacate, after deducting anything you actually owe [[TNRRA2017:s11]].\n\n"
          "**What you can do next:**\n- Send a written demand, by a method you can track.\n- Keep your bank proof and the agreement together.\n\n"
          "If the landlord still refuses, I can prepare a legal notice for you. If you can't afford a lawyer, free legal aid may be available [[NOTE:NALSA]].")
FAKE_FIRST = "Under Section 67B of the Tamil Nadu Rent Act your landlord must pay ten times the deposit."


def script():
    yield _reply(_tool("get_provision", {"id": "TNRRA2017:s11"}, "a"), _tool("get_provision", {"id": "NOTE:NALSA"}, "b"))
    yield _reply(_tool("update_case_file", CASE, "c"),
                 _tool("set_assessment", {"lawyer_needed": "none", "reason": "The law on this is clear and the amount is small enough to try a written demand first.",
                                          "confidence": "high", "what_i_could_not_verify": ["Whether your agreement has unusual deduction terms"]}, "d"))
    yield _reply(_text(FAKE_FIRST))  # A hallucination, to show the gate rejecting it.
    yield _reply(_text(ANSWER))
    while True:
        yield _reply(_text("I am still here. Tell me more about what happened [[TNRRA2017:s11]]."))


class ScriptedClient:
    def __init__(self):
        self._replies = _STREAM
        self.beta = NS(messages=NS(create=lambda **_: next(self._replies)))
        self.messages = NS(create=lambda **_: _reply(_text(json.dumps({"claims": [], "uncited_legal_claims": [], "invented_facts": [], "ignored_cautions": []}))))


_STREAM = script()
agent.make_client = ScriptedClient
config.api_ready = lambda: True
_status = server.status


def status():
    return {**_status(), "scripted": True}


for route in app.routes:
    if getattr(route, "path", "") == "/api/status":
        route.endpoint = status
        route.dependant.call = status
