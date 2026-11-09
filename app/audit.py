"""Second gate: does the cited text actually support what the answer claims?

The deterministic verifier proves a citation is real. It cannot tell whether a real
section says what the answer claims, which is the subtler way models mislead. So a
separate model call reads each claim beside the exact text cited, with no conversation
history and no stake in the draft, and returns a structured verdict.

Claims are judged only against the supplied text, not against the auditor's own memory
of the law. That matters: the auditor can be wrong about law too.
"""
from __future__ import annotations

import json

from . import config
from .corpus import CITE_RE, library
from .llm import STATUS_ERRORS
from .verifier import Issue, split_paragraphs

SCHEMA = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "paragraph": {"type": "integer"},
                    "claim": {"type": "string"},
                    "citations": {"type": "array", "items": {"type": "string"}},
                    "verdict": {"type": "string", "enum": ["supported", "partly_supported", "not_supported"]},
                    "reason": {"type": "string"},
                    "exact_quote": {"type": "string"},
                },
                "required": ["paragraph", "claim", "citations", "verdict", "reason", "exact_quote"],
                "additionalProperties": False,
            },
        },
        "uncited_legal_claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"paragraph": {"type": "integer"}, "claim": {"type": "string"}, "exact_quote": {"type": "string"}},
                "required": ["paragraph", "claim", "exact_quote"],
                "additionalProperties": False,
            },
        },
        "invented_facts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"paragraph": {"type": "integer"}, "fact": {"type": "string"}, "exact_quote": {"type": "string"}},
                "required": ["paragraph", "fact", "exact_quote"],
                "additionalProperties": False,
            },
        },
        "ignored_cautions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"paragraph": {"type": "integer"}, "problem": {"type": "string"}, "exact_quote": {"type": "string"}},
                "required": ["paragraph", "problem", "exact_quote"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["claims", "uncited_legal_claims", "invented_facts", "ignored_cautions"],
    "additionalProperties": False,
}

SYSTEM = """You audit an AI legal helper's draft answer for an Indian user. You have the draft and the exact legal text it cites. The draft may be in any Indian language; audit it in that language and write your output in English.

Judge ONLY against the supplied source text. Do not use what you remember about the law. If the text does not say it, it is not supported, even if you believe it is true.

For each statement of law carrying a citation token like [[ID]], give a verdict:
- supported: the cited text says this, or it is a fair plain-language summary of it.
- partly_supported: the core is right but the draft overstates it, leaves out a condition that matters here, or applies it more broadly than the text allows.
- not_supported: the cited text does not say this, or says something different.

Then list:
- uncited_legal_claims: statements that a law gives a right, sets a duty, sets a deadline or a penalty, or names a forum, with no citation. Do not list practical tips (keep receipts, send by registered post), questions to the user, facts the user gave, or plain statements of what the user could try.
- invented_facts: specific facts about this person's own situation that the draft states as true but that do not appear in user_facts, such as "no dues are pending" or "I handed over the keys". Ignore [PLACEHOLDERS], questions, conditional wording ("if you owe nothing"), and today's date.
- ignored_cautions: places where the draft breaks a CAUTION printed beside a source, such as presenting a model law as binding, or using an outdated limit.

For every item, set exact_quote to the one full sentence at fault, copied word for word from the draft in its original language.

Be precise, not picky:
- A helpful simplification that stays true is fine. Use partly_supported only when a missing condition could actually mislead this person.
- A point already cited elsewhere in the draft counts as cited. Do not list it again as uncited.
- A suggested next step ("if he still doesn't pay, apply to the Rent Court") is advice, not an uncited claim, when the draft supports the forum elsewhere.
- Dates and day counts in app_calculations were worked out by the app's date tool from the person's facts. They are not invented.
- Do not require the draft to discuss where a source file came from.
Number paragraphs from 1, as they are numbered in the draft."""


def _source_block(cited: list[str]) -> str:
    lib = library()
    parts = []
    for pid in cited:
        if not lib.get(pid):
            continue
        caution = lib.caution(pid)
        head = f'<source id="{pid}" label="{lib.label(pid)}">'
        if caution:
            head += f"\nCAUTION: {caution}"
        parts.append(f"{head}\n{lib.full_text(pid)}\n</source>")
    return "\n".join(parts)


def audit_answer(client, text: str, user_facts: str = "", calculations: str = "") -> tuple[list[Issue], dict]:
    """Return (blocking issues, raw verdict JSON). Raises on API failure, so callers can report that the audit did not run."""
    paragraphs = split_paragraphs(text)
    numbered = "\n\n".join(f"[Paragraph {i + 1}]\n{p}" for i, p in enumerate(paragraphs))
    cited = sorted(set(CITE_RE.findall(text)))
    prompt = (f"<user_facts>\n{user_facts[-6000:]}\n</user_facts>\n\n"
              f"<app_calculations>\n{calculations[-1500:]}\n</app_calculations>\n\n<sources>\n{_source_block(cited)}\n</sources>\n\n"
              f"<draft>\n{numbered}\n</draft>")
    # Stronger models have small free quotas, so a public demo falls back rather than skipping the audit.
    models = [config.AUDIT_MODEL] + [m for m in config.AUDIT_FALLBACKS if m != config.AUDIT_MODEL]
    for position, model in enumerate(models):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=config.MAX_TOKENS,
                system=SYSTEM,
                output_config={"effort": config.AUDIT_EFFORT, "format": {"type": "json_schema", "schema": SCHEMA}},
                messages=[{"role": "user", "content": prompt}],
            )
            break
        except STATUS_ERRORS:
            if position == len(models) - 1:
                raise
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError(f"audit could not finish: {response.stop_reason}")
    verdict = json.loads(next(b.text for b in response.content if b.type == "text"))
    issues = []
    for claim in verdict["claims"]:
        if claim["verdict"] == "not_supported":
            issues.append(Issue("claim_not_supported", f"Paragraph {claim['paragraph']}: \"{claim['claim'][:160]}\" is not supported by {claim['citations']}. {claim['reason']}", claim["paragraph"] - 1, quote=claim.get("exact_quote")))
        elif claim["verdict"] == "partly_supported":
            issues.append(Issue("claim_overstated", f"Paragraph {claim['paragraph']}: \"{claim['claim'][:160]}\" goes beyond {claim['citations']}. {claim['reason']}", claim["paragraph"] - 1, quote=claim.get("exact_quote")))
    for item in verdict["uncited_legal_claims"]:
        issues.append(Issue("uncited_legal_claim", f"Paragraph {item['paragraph']}: \"{item['claim'][:160]}\" states law with no citation. Find and cite the provision, say you could not verify it, or remove it.", item["paragraph"] - 1, quote=item.get("exact_quote")))
    for item in verdict["invented_facts"]:
        issues.append(Issue("invented_fact", f"Paragraph {item['paragraph']}: \"{item['fact'][:160]}\" is stated as fact, but the person never said it. Ask them, make it conditional, or use a [PLACEHOLDER].", item["paragraph"] - 1, quote=item.get("exact_quote")))
    for item in verdict["ignored_cautions"]:
        issues.append(Issue("ignored_caution", f"Paragraph {item['paragraph']}: {item['problem']}", item["paragraph"] - 1, quote=item.get("exact_quote")))
    usage = getattr(response, "usage", None)
    verdict["_usage"] = {"input": getattr(usage, "input_tokens", 0), "output": getattr(usage, "output_tokens", 0)}
    verdict["_model"] = model
    return issues, verdict
