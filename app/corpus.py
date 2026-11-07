"""The verified legal library: statutes, cautions, repeal records, practical notes.

This module is the single source of truth for what counts as "real law" in the app.
A citation is valid only if it resolves to a record loaded here.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from . import config

CITE_RE = re.compile(r"\[\[([A-Za-z0-9]+:[A-Za-z0-9_]+)\]\]")


class Library:
    def __init__(self):
        self.records: dict[str, dict] = {}
        for line in (config.CORPUS / "provisions.jsonl").read_text().splitlines():
            rec = json.loads(line)
            self.records[rec["id"]] = rec
        ann = json.loads((config.CORPUS / "annotations.json").read_text())
        self.cautions = ann["cautions"]
        self.source_cautions = ann["source_cautions"]
        self.repealed_laws = ann["repealed_laws"]
        self.manifest = json.loads((config.CORPUS / "manifest.json").read_text())
        self.sources = {m["id"]: m for m in self.manifest}
        notes = json.loads((config.CORPUS / "practical_notes.json").read_text())
        # A note that failed its check against the official page is never served.
        self.notes = {n["id"]: n for n in notes if n.get("verified")}

    def get(self, pid: str) -> dict | None:
        return self.records.get(pid) or self.notes.get(pid)

    def is_note(self, pid: str) -> bool:
        return pid.startswith("NOTE:")

    def caution(self, pid: str) -> str:
        rec = self.records.get(pid)
        if not rec:
            return ""
        # A non-government source copy is labelled in the citation viewer, so the reply needn't mention it.
        parts = [self.cautions.get(pid, ""), self.source_cautions.get(rec["source_id"], "")]
        return " ".join(p for p in parts if p)

    def label(self, pid: str) -> str:
        """Plain-language citation, e.g. 'Section 138, Negotiable Instruments Act, 1881'."""
        if self.is_note(pid):
            note = self.notes.get(pid)
            return note["title"] if note else pid
        rec = self.records.get(pid)
        if not rec:
            return pid
        if rec["unit"] == "Schedule":
            return f"Schedule, {rec['act']}"
        return f"{rec['unit']} {rec['number']}, {rec['act']}"

    def short_label(self, pid: str) -> str:
        """Compact form for chips in the chat, e.g. 'S.138, Negotiable Instruments Act 1881'."""
        rec = self.records[pid]
        abbr = {"Section": "S.", "Rule": "R.", "Article": "Art. ", "Order": "O."}.get(rec["unit"])
        return f"{abbr}{rec['number']}, {rec['act_short']}" if abbr else f"Schedule, {rec['act_short']}"

    def full_text(self, pid: str) -> str:
        if self.is_note(pid):
            note = self.notes.get(pid)
            return note["fact"] if note else ""
        rec = self.records.get(pid)
        return f"{rec['heading']}. {rec['text']}" if rec else ""

    def public_record(self, pid: str) -> dict | None:
        """What the UI shows when someone clicks a citation."""
        if self.is_note(pid):
            n = self.notes.get(pid)
            return n and {"id": pid, "label": n["title"], "short": n["title"], "kind": "note", "text": n["fact"], "text_hi": n.get("fact_hi"),
                          "url": n["source_url"], "retrieved": n.get("checked_on"), "page": None,
                          "caution": "", "status": "in_force", "jurisdiction": "India"}
        r = self.records.get(pid)
        return r and {"id": pid, "label": self.label(pid), "short": self.short_label(pid), "kind": "provision", "heading": r["heading"],
                      "text": r["text"], "url": r["url"], "retrieved": r["retrieved"], "page": r["page"],
                      "sha256": r["sha256"], "caution": self.caution(pid), "status": r["status"], "mirror": r.get("mirror", False),
                      "jurisdiction": r["jurisdiction"]}

    def coverage_text(self) -> str:
        """The library's contents, listed in the system prompt so the model knows its limits."""
        lines = []
        for m in self.manifest:
            count = sum(1 for r in self.records.values() if r["source_id"] == m["id"])
            if count:
                note = "" if m["status"] == "in_force" else " [MODEL LAW, binds no one unless adopted]"
                lines.append(f"- {m['title']} ({m['jurisdiction']}){note}")
        return "\n".join(lines)


@lru_cache(maxsize=1)
def library() -> Library:
    return Library()
