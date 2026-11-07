"""Tools the model can call. All run on our side, and all are strict-schema."""
from __future__ import annotations

import calendar
import datetime as dt
import json

from .corpus import library
from .retrieval import index
from .triage import stronger
from .verifier import fact_check_document, verify

DOC_TYPES = ["legal_notice", "consumer_complaint", "reply_to_notice", "wage_claim_application", "demand_letter", "complaint_letter", "evidence_checklist", "other"]

NULLABLE_STRING = {"type": ["string", "null"]}

TOOLS = [
    {
        "name": "search_law",
        "description": "Search the verified library of Indian statutes. Returns excerpts, exact ids, and any caution. Write the query in English using legal or plain words, even if the user wrote in another language. Run several searches with different words if the first misses. Pass the user's State once known, so other States' laws are excluded.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for, e.g. 'refund of security deposit by landlord'"},
                "state": {**NULLABLE_STRING, "description": "The user's Indian State or city, or null if not known"},
            },
            "required": ["query", "state"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_provision",
        "description": "Read the full text of a provision or practical note by its exact id, such as 'COW2019:s17', 'LIM1963:art55', 'CPC1908:o37' or 'NOTE:NALSA'. Always read the full text before relying on a provision, because search excerpts can cut off conditions and exceptions.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"id": {"type": "string"}},
            "required": ["id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "date_calc",
        "description": "Date arithmetic. Add a period to a start date and get the days left from today. Use this instead of working out any deadline or date yourself.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "start_date": {"type": "string", "description": "YYYY-MM-DD"},
                "amount": {"type": "integer"},
                "unit": {"type": "string", "enum": ["days", "months", "years"]},
            },
            "required": ["start_date", "amount", "unit"],
            "additionalProperties": False,
        },
    },
    {
        "name": "update_case_file",
        "description": "Save what you know about the case. Call it whenever you learn new facts. Each list you pass replaces the old list, so pass the whole list. Pass null for anything you are not changing. Record only what the user said or what an uploaded document shows.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "problem_type": {**NULLABLE_STRING, "description": "tenancy_deposit, consumer_refund, unpaid_salary, notice_received, cheque_bounce, or other"},
                "state": {**NULLABLE_STRING, "description": "The Indian State or city"},
                "summary": {**NULLABLE_STRING, "description": "Two or three sentences on the problem, in English"},
                "parties": {"type": ["array", "null"], "items": {"type": "object", "properties": {"role": {"type": "string"}, "name": {"type": "string"}}, "required": ["role", "name"], "additionalProperties": False}},
                "timeline": {"type": ["array", "null"], "items": {"type": "object", "properties": {"date": {"type": "string", "description": "As exact as known, or 'unknown'"}, "event": {"type": "string"}, "source": {"type": "string", "enum": ["user", "document"]}}, "required": ["date", "event", "source"], "additionalProperties": False}},
                "amounts": {"type": ["array", "null"], "items": {"type": "object", "properties": {"label": {"type": "string"}, "amount_inr": {"type": "string"}}, "required": ["label", "amount_inr"], "additionalProperties": False}},
                "evidence": {"type": ["array", "null"], "items": {"type": "object", "properties": {"item": {"type": "string"}, "status": {"type": "string", "enum": ["have", "missing", "unclear"]}, "why_it_matters": {"type": "string"}}, "required": ["item", "status", "why_it_matters"], "additionalProperties": False}},
                "deadlines": {"type": ["array", "null"], "items": {"type": "object", "properties": {"what": {"type": "string"}, "date": {"type": "string"}, "basis": {"type": "string", "description": "Citation id, or 'user' or 'document'"}}, "required": ["what", "date", "basis"], "additionalProperties": False}},
                "open_questions": {"type": ["array", "null"], "items": {"type": "string"}},
                "document_references": {"type": ["array", "null"], "items": {"type": "string"}, "description": "Laws cited in an uploaded document, exactly as written there, verified or not."},
            },
            "required": ["problem_type", "state", "summary", "parties", "timeline", "amounts", "evidence", "deadlines", "open_questions", "document_references"],
            "additionalProperties": False,
        },
    },
    {
        "name": "set_assessment",
        "description": "Record your honest view of how this person stands. Call it in every turn with substantive advice.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "lawyer_needed": {"type": "string", "enum": ["none", "recommended", "urgent", "emergency"]},
                "reason": {"type": "string", "description": "One plain sentence, in the user's language"},
                "confidence": {"type": "string", "enum": ["high", "medium", "low"], "description": "How well the law you could verify settles this question"},
                "what_i_could_not_verify": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["lawyer_needed", "reason", "confidence", "what_i_could_not_verify"],
            "additionalProperties": False,
        },
    },
    {
        "name": "save_document",
        "description": "Save a draft for the user to download. The body is checked by code: law must use [[ID]] citation tokens you have read, and every date and amount must come from the user. Use [PLACEHOLDER IN CAPITALS] for anything unknown. If checks fail, you get a list of problems to fix, and nothing is saved until it passes.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "doc_type": {"type": "string", "enum": DOC_TYPES},
                "title": {"type": "string"},
                "body": {"type": "string", "description": "The full document text. Separate paragraphs with a blank line. Lines starting with '# ' are headings."},
                "fill_in": {"type": "array", "items": {"type": "string"}, "description": "What the user must complete or check before using it"},
            },
            "required": ["doc_type", "title", "body", "fill_in"],
            "additionalProperties": False,
        },
    },
]


def add_period(start: dt.date, amount: int, unit: str) -> dt.date:
    if unit == "days":
        return start + dt.timedelta(days=amount)
    months = amount if unit == "months" else amount * 12
    total = start.month - 1 + months
    year, month = start.year + total // 12, total % 12 + 1
    return start.replace(year=year, month=month, day=min(start.day, calendar.monthrange(year, month)[1]))


class ToolBox:
    """Runs tools for one session and records what the model has actually read."""

    def __init__(self, session, today: dt.date | None = None):
        self.session = session
        self.today = today or dt.date.today()
        self.events: list[dict] = []

    def run(self, name: str, args: dict) -> tuple[str, bool]:
        """Return (result text, is_error)."""
        handler = getattr(self, f"_{name}", None)
        if handler is None:
            return f"Unknown tool {name}", True
        try:
            return handler(args)
        except (KeyError, ValueError, TypeError) as exc:
            return f"Bad input: {exc}", True

    def _search_law(self, args):
        state = args.get("state") or self.session["case_file"].get("state")
        hits = index().search(args["query"], state=state)
        for hit in hits:
            self.session["read_ids"].append(hit["id"])
        if not hits:
            return "No matching provisions in the library. Tell the user you could not find this in the laws you can verify.", False
        return json.dumps(hits, ensure_ascii=False), False

    def _get_provision(self, args):
        lib = library()
        pid = args["id"].strip().strip("[]")
        rec = lib.get(pid)
        if rec is None:
            return f"No provision with id '{pid}' exists in the library. It may not exist at all. Do not cite it. Use search_law to look for the right one.", True
        self.session["read_ids"].append(pid)
        if lib.is_note(pid):
            return json.dumps({"id": pid, "title": rec["title"], "fact": rec["fact"], "source": rec["source_url"]}, ensure_ascii=False), False
        if rec.get("repealed"):
            return json.dumps({"id": pid, "status": "REPEALED OR OMITTED. Do not rely on this.", "text": rec["text"]}, ensure_ascii=False), False
        return json.dumps({"id": pid, "citation": lib.label(pid), "applies_in": rec["jurisdiction"], "caution": lib.caution(pid),
                           "heading": rec["heading"], "text": rec["text"]}, ensure_ascii=False), False

    def _date_calc(self, args):
        start = dt.date.fromisoformat(args["start_date"])
        end = add_period(start, int(args["amount"]), args["unit"])
        left = (end - self.today).days
        amount, unit = args["amount"], args["unit"]
        since = (self.today - start).days
        result = {"start": start.isoformat(), "period": f"{amount} {unit}", "end_date": end.isoformat(), "today": self.today.isoformat(),
                  "days_from_today": left, "already_passed": left < 0, "days_since_start": since}
        self.session["tool_numbers"] += f" {amount} {unit} {end.strftime('%d %B %Y')} {abs(left)} days {abs(since)} days"
        return json.dumps(result), False

    def _update_case_file(self, args):
        case = self.session["case_file"]
        for key, value in args.items():
            if value is not None:
                case[key] = value
        # Laws named in an uploaded notice may be discussed, but only if a document exists.
        if self.session.get("has_upload") and args.get("document_references"):
            self.session["user_text"] += " " + " ".join(args["document_references"])
        self.events.append({"type": "case_file", "case_file": case})
        return "Saved.", False

    def _set_assessment(self, args):
        level = stronger(args["lawyer_needed"], self.session["triage"]["level"])
        assessment = {**args, "lawyer_needed": level}
        self.session["assessment"] = assessment
        self.events.append({"type": "assessment", "assessment": assessment})
        return "Saved.", False

    def _save_document(self, args):
        body = args["body"]
        state = self.session["case_file"].get("state")
        report = verify(body, set(self.session["read_ids"]), self.session["user_text"], state, self.session["tool_numbers"])
        issues = report.issues + fact_check_document(body, self.session["user_text"], self.session["tool_numbers"])
        blocking = [i for i in issues if i.blocking]
        if blocking:
            self.session["doc_rejections"] = self.session.get("doc_rejections", 0) + 1
            return "NOT SAVED. Fix these and call save_document again:\n" + "\n".join(f"- [{i.code}] {i.detail}" for i in blocking), True
        doc = {"id": f"doc{len(self.session['documents']) + 1}", "type": args["doc_type"], "title": args["title"], "body": body,
               "fill_in": args["fill_in"], "citations": report.cited}
        self.session["documents"].append(doc)
        self.events.append({"type": "document", "document": doc})
        return f"Saved as {doc['id']}. Tell the user it is ready, explain it in their language, and list what they must fill in.", False
