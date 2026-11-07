"""The gate between the model and the user: nothing reaches a person until it passes.

Models invent laws, section numbers and judgments, and they do it confidently. So the
model's word is not trusted. Each draft answer is checked by ordinary code against the
library, then each cited claim is checked against the exact text it cites.

Layer 1, deterministic (this file, no model involved):
  * every [[citation]] must exist in the library and must have been read this session
  * every free-text legal reference must be backed by a citation or come from the user
  * case-law citations are always blocked, because the library has no judgments
  * laws outside the library and repealed laws are blocked unless handled correctly
  * time limits and money amounts must appear in a cited source, or in the user's facts
  * quoted statutory wording must match the source
  * another State's law cannot be applied to this user

Layer 2, model-based (audit.py): is each claim actually supported by the text it cites?

Both layers feed a repair loop. Whatever still fails is removed and disclosed.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

from .corpus import CITE_RE, library
from .retrieval import STATE_SOURCES, normalise_state

# Words for "section", "rule" and so on, in the languages the app supports.
SECTION_WORDS = (r"sections?|secs?\.?|u/s|s\.|धारा(?:ओं)?|धाराएं|दफ़ा|दफा|कलम|ধারা|பிரிவு|செக்ஷன்|సెక్షన్|ಸೆಕ್ಷನ್|ವಿಭಾಗ|"
                 r"വകുപ്പ്|સેક્શન|કલમ|ਧਾਰਾ|سیکشن|دفعہ|ଧାରା")
RULE_WORDS = r"rules?|नियम|ನಿಯಮ|விதி|నియమం"
ARTICLE_WORDS = r"articles?|अनुच्छेद"
ORDER_WORDS = r"orders?|आदेश"
NUM_LIST = r"(\d{1,3}[A-Z]{0,2}(?:\s*\([0-9a-zA-Z]+\))*(?:\s*(?:,|and|or|&|to|और|तथा|एवं|व)\s*\d{1,3}[A-Z]{0,2}(?:\s*\([0-9a-zA-Z]+\))*)*)"

REF_RE = re.compile(rf"(?<![\w])({SECTION_WORDS}|{RULE_WORDS}|{ARTICLE_WORDS})\s*{NUM_LIST}", re.I)
# "138 धारा" / "धारा 138 के तहत": Indian languages can put the number first.
REF_AFTER_RE = re.compile(r"(?<![\w.])(\d{1,3}[A-Z]{0,2})\s*(धारा|कलम|ধারা|பிரிவு)")
ORDER_RE = re.compile(rf"(?<![\w])(?:{ORDER_WORDS})\s+([IVXLC]{{1,7}})(?:\s+rule\s+\d+)?\b", re.I)

CASE_PATTERNS = [
    re.compile(r"\bAIR\s*\d{4}\b"),
    re.compile(r"\(\d{4}\)\s*\d*\s*(?:SCC|SCR|SCALE|Bom|Del|Mad|Cal)\b"),
    re.compile(r"\b\d{4}\s*(?:SCC|INSC|SCR)\b"),
    re.compile(r"\bSCC\s+(?:OnLine|\d)"),
    re.compile(r"\b[A-Z][\w.&'-]+(?:\s+[A-Z][\w.&'-]+){0,4}\s+(?:v\.|vs\.?|versus)\s+[A-Z][\w.&'-]+"),
    re.compile(r"[ऀ-ॿ]+\s+(?:बनाम|वर्सेस)\s+[ऀ-ॿ]+"),
    # A court "holding" something is a claim about a judgment even with no citation.
    re.compile(r"\b(?:Supreme Court|High Court|NCDRC|National Commission|SC|HC)\b[^.\n]{0,50}\b(?:held|ruled|has said|decided|observed|judgment|judgement|landmark)\b", re.I),
    re.compile(r"(?:सुप्रीम कोर्ट|उच्चतम न्यायालय|हाई कोर्ट|उच्च न्यायालय)[^।\n]{0,60}(?:फैसला|निर्णय|कहा है|माना है)"),
]
# Honest statements about having no judgments are allowed.
CASE_DISCLAIMER = re.compile(r"(?:not|no|never|cannot|can't|don't|do not|nahi|nahin|नहीं|न\s).{0,70}(?:cite|judgment|judgement|case|फैसल|निर्णय|mamle|मामल)|"
                             r"(?:cite|judgment|judgement|case|फैसल|निर्णय|मामल).{0,70}(?:not|cannot|can't|don't|do not|nahi|नहीं)", re.I | re.S)

# Plain statements of law, or that something can't happen, need a source beside them. Caught by
# code because a weak audit model lets them through, and they do the most harm when wrong.
LAW_ASSERTION = re.compile(
    r"\b(under (the |indian |general |your )?(law|contract|property|legal)|indian law|by law|legally|illegal(ly)?|unlawful(ly)?|not permitted|"
    r"against the law|courts? (will|accept|must|view|treat|always|never)|police (will|view|treat)|"
    r"(will|can) ?not be arrested|won't be arrested|no one can (arrest|jail)|kanoon ke tahat|kanoonan|kanooni taur)\b|"
    r"कानून के तहत|कानूनन|भारतीय कानून|गैरकानूनी|अवैध", re.I)
NONEXISTENCE = re.compile(
    r"\b(does ?n[o']t exist|does not exist|(is|are|completely|totally) (fabricated|fake|invented)|no such (law|section|act)|there is no section)\b|मौजूद नहीं है", re.I)
HONEST_LIMIT = re.compile(
    r"(could ?n.t|could not|can ?not|can't|unable to|not able to|don't|do not)\s+(verify|find|confirm|check|see)|not in (my|the) (verified )?library|outside (what|the laws)|"
    r"सत्यापित नहीं|नहीं मिला|verify nahi|nahi mila", re.I)
HINGLISH_WORDS = set("hai hain nahi nahin kya aap aapke aapka aapko mein ke ki ka ko se toh ho kar karna hoga bhi lekin agar mujhe tum tumhara maine hum yeh woh hota raha rahi".split())

ACT_NO_YEAR_RE = re.compile(r"\b((?:[A-Z][a-z]+\s+(?:and\s+|of\s+)?){2,6}Act)\b(?!,?\s+\d{4})")
ACT_NAME_RE = re.compile(r"\b((?:[A-Z][a-zA-Z()&'-]+\s+){1,8}(?:Act|Code|Sanhita|Adhiniyam|Rules|Regulations)),?\s+(\d{4})\b")

NUMBER_WORDS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9,
                "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16,
                "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30, "forty": 40, "forty-five": 45,
                "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90, "hundred": 100, "a": 1, "an": 1,
                "एक": 1, "दो": 2, "तीन": 3, "चार": 4, "पांच": 5, "पाँच": 5, "छह": 6, "छः": 6, "सात": 7, "आठ": 8, "नौ": 9, "दस": 10,
                "ग्यारह": 11, "बारह": 12, "पंद्रह": 15, "पन्द्रह": 15, "बीस": 20, "तीस": 30, "चालीस": 40, "पैंतालीस": 45, "पचास": 50, "साठ": 60, "नब्बे": 90, "सौ": 100,
                "ek": 1, "do": 2, "teen": 3, "chaar": 4, "char": 4, "paanch": 5, "panch": 5, "chhe": 6, "saat": 7, "aath": 8, "nau": 9, "das": 10,
                "pandrah": 15, "bees": 20, "tees": 30}
UNITS = {"day": "day", "days": "day", "working day": "day", "working days": "day", "दिन": "day", "दिनों": "day",
         "week": "week", "weeks": "week", "हफ्ते": "week", "सप्ताह": "week", "हफ़्ते": "week",
         "month": "month", "months": "month", "महीने": "month", "महीना": "month", "माह": "month", "महीनों": "month",
         "year": "year", "years": "year", "साल": "year", "वर्ष": "year", "वर्षों": "year", "सालों": "year",
         "din": "day", "dino": "day", "hafte": "week", "hafta": "week", "mahine": "month", "mahina": "month", "mahino": "month", "saal": "year"}
_WORD_ALT = "|".join(sorted(map(re.escape, NUMBER_WORDS), key=len, reverse=True))
_UNIT_ALT = "|".join(sorted(map(re.escape, UNITS), key=len, reverse=True))
# English number words use \b, which does not work for Devanagari matras, hence the look-arounds.
DURATION_RE = re.compile(rf"(?<![\wऀ-ॿ])(\d{{1,4}}|{_WORD_ALT})[\s-]*(?:\(\d+\)\s*)?({_UNIT_ALT})(?![a-z])", re.I)
MONEY_RE = re.compile(r"(?:₹|rs\.?|inr|rupees?|रुपये|रु\.?)\s*([\d,]+(?:\.\d+)?)\s*(lakhs?|crores?|लाख|करोड़|thousand|हज़ार|हजार)?"
                      r"|([\d,]+(?:\.\d+)?)\s*(lakhs?|crores?|लाख|करोड़)"
                      r"|([\d,]+(?:\.\d+)?)\s*(?:/-|rs\.?|rupees?|rupaye|rupiya|रुपये|रुपए|रु\.?|টাকা)", re.I)
MONEY_WORDS_RE = re.compile(rf"(?<![\w])({_WORD_ALT})\s*(lakhs?|crores?|thousand)(?:\s+rupees)?|rupees\s+({_WORD_ALT})(?![\w\u0900-\u097F])\s*(lakhs?|crores?|thousand)?", re.I)
MULTIPLIER = {"lakh": 100_000, "lakhs": 100_000, "लाख": 100_000, "crore": 10_000_000, "crores": 10_000_000, "करोड़": 10_000_000,
              "thousand": 1000, "हज़ार": 1000, "हजार": 1000}


@dataclass
class Issue:
    code: str
    detail: str
    paragraph: int | None = None
    blocking: bool = True
    quote: str | None = None  # The exact sentence at fault, when the audit could quote it.

    def as_dict(self):
        return {"code": self.code, "detail": self.detail, "paragraph": self.paragraph, "blocking": self.blocking, "quote": self.quote}


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)
    cited: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not any(i.blocking for i in self.issues)

    def feedback(self) -> str:
        return "\n".join(f"- [{i.code}] {i.detail}" for i in self.issues if i.blocking)


def to_ascii_digits(text: str) -> str:
    return "".join(str(unicodedata.digit(c)) if c.isdigit() and not c.isascii() else c for c in text)


def squash(text: str) -> str:
    """For quote comparison: PDF extraction scatters spaces, so spacing is ignored."""
    return re.sub(r"[\s\W_]+", "", text).lower()


def durations(text: str) -> set[tuple[int, str]]:
    found = set()
    for m in DURATION_RE.finditer(to_ascii_digits(text)):
        raw, unit = m.group(1).lower(), m.group(2).lower()
        value = int(raw) if raw.isdigit() else NUMBER_WORDS.get(raw)
        if value is not None:
            found.add((value, UNITS[unit]))
    return found


def _money_value(number: str, unit: str | None) -> int:
    base = float(number.replace(",", "") or 0)
    return int(base * MULTIPLIER.get((unit or "").lower(), 1))


def money(text: str) -> set[int]:
    text = to_ascii_digits(text)
    found = set()
    for m in MONEY_RE.finditer(text):
        number, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4)) if m.group(3) else (m.group(5), None)
        if number and number.strip(","):
            found.add(_money_value(number, unit))
    for m in MONEY_WORDS_RE.finditer(text):
        word, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
        if word and word.lower() in NUMBER_WORDS:
            found.add(NUMBER_WORDS[word.lower()] * MULTIPLIER.get((unit or "").lower(), 1))
    return found


def split_paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


def _base_numbers(raw: str) -> list[str]:
    raw = re.sub(r"\([^)]*\)", "", raw)
    return [n.upper() for n in re.findall(r"\d{1,3}[A-Za-z]{0,2}", raw)]


def _token_number(pid: str) -> str:
    return re.sub(r"^[a-z]+", "", pid.split(":", 1)[1]).upper()


def _roman_to_int(s: str) -> int:
    vals = {"M": 1000, "D": 500, "C": 100, "L": 50, "X": 10, "V": 5, "I": 1}
    total = 0
    for a, b in zip(s, s[1:] + " "):
        total += -vals[a] if b in vals and vals[b] > vals[a] else vals[a]
    return total


def verify(text: str, read_ids: set[str], user_text: str = "", state: str | None = None,
           tool_numbers: str = "", require_citation_for_law: bool = True) -> Report:
    """Run every deterministic check.

    text:        the draft answer or document
    read_ids:    provisions the model actually retrieved in this session
    user_text:   what the user said and uploaded, the only other source of facts
    state:       the user's State, if known
    tool_numbers: output from tools such as date_calc, a legitimate source of numbers
    """
    lib = library()
    report = Report()
    state = normalise_state(state)
    user_ascii = to_ascii_digits(user_text)
    paragraphs = split_paragraphs(text)
    all_tokens = CITE_RE.findall(text)
    report.cited = sorted(set(all_tokens))

    # 1. Each citation must resolve, must have been read, and must be usable.
    for pid in report.cited:
        rec = lib.get(pid)
        if rec is None:
            report.issues.append(Issue("citation_not_in_library", f"[[{pid}]] does not exist in the library. Remove it or search for the correct provision."))
            continue
        if pid not in read_ids:
            report.issues.append(Issue("citation_not_read", f"[[{pid}]] was never retrieved in this conversation. Call get_provision on it and re-check your claim against the text."))
        if not lib.is_note(pid):
            if rec.get("repealed"):
                report.issues.append(Issue("citation_repealed_provision", f"[[{pid}]] is marked repealed or omitted in the Act. Do not rely on it."))
            own_state = STATE_SOURCES.get(rec["source_id"])
            if own_state and state and own_state != state:
                report.issues.append(Issue("wrong_state_law", f"[[{pid}]] is {own_state.title()} law, but this user is in {state.title()}. Remove it."))

    # 2. Facts about the cited sources: the allowed set of numbers.
    source_blob = " ".join(lib.full_text(p) for p in report.cited if lib.get(p))
    allowed_durations = durations(source_blob) | durations(user_ascii) | durations(tool_numbers)
    allowed_money = money(source_blob) | money(user_ascii) | money(tool_numbers)

    for i, para in enumerate(paragraphs):
        tokens = CITE_RE.findall(para)
        bare = CITE_RE.sub(" ", para)
        bare_ascii = to_ascii_digits(bare)
        para_numbers = {_token_number(t) for t in tokens if not lib.is_note(t)}
        doc_tokens = {t for t in report.cited if not lib.is_note(t)}
        doc_numbers = {_token_number(t) for t in doc_tokens}

        # 3. Free-text references ("Section 138") must match a citation or the user's own words.
        refs = []
        for m in REF_RE.finditer(bare_ascii):
            if m.group(1).lower() in ("s.",) and not re.search(r"\d", m.group(2)):
                continue
            refs.extend(_base_numbers(m.group(2)))
        refs.extend(m.group(1).upper() for m in REF_AFTER_RE.finditer(bare_ascii))
        for num in refs:
            if num in para_numbers:
                continue
            if _mentioned_by_user(num, user_ascii):
                continue
            if num in doc_numbers:
                report.issues.append(Issue("reference_without_citation", f"Paragraph {i + 1} mentions section/rule {num} but has no citation beside it. Put the matching [[ID]] in the same paragraph.", i))
            else:
                report.issues.append(Issue("unverified_reference", f"Paragraph {i + 1} mentions section/rule {num}, which matches no retrieved provision and did not come from the user. Search for it, or delete it.", i))
        for m in ORDER_RE.finditer(bare_ascii):
            num = str(_roman_to_int(m.group(1).upper()))
            if num not in para_numbers and not re.search(rf"order\s+{m.group(1)}\b", user_ascii, re.I):
                report.issues.append(Issue("unverified_reference", f"Paragraph {i + 1} mentions Order {m.group(1)} without a matching citation.", i))

        # 4. Judgments are never allowed. The library has none, so a citation could not be checked.
        if not CASE_DISCLAIMER.search(bare):
            for pat in CASE_PATTERNS:
                hit = pat.search(bare)
                if hit and hit.group(0).lower() not in user_text.lower():
                    report.issues.append(Issue("case_law_citation", f"Paragraph {i + 1} refers to a court decision ({hit.group(0)[:70]!r}). The library holds no judgments, so this cannot be verified. Delete it and tell the user a lawyer can find relevant judgments.", i))
                    break

        # 5. Named laws must be in the library, or come from the user.
        for m in ACT_NAME_RE.finditer(bare):
            name = re.sub(r"^(?:The|Under|Of|In|And|Per|By|From|With|See|As)\s+", "", m.group(0).strip(), flags=re.I)
            if _act_known(name, m.group(2), lib) or name.lower() in user_text.lower():
                continue
            if _is_repealed_name(name, lib):
                continue  # Handled by check 6, with a more useful message.
            report.issues.append(Issue("law_not_in_library", f"Paragraph {i + 1} names '{name}', which is not in the verified library. Delete it, or say plainly that you could not verify it.", i))

        for m in ACT_NO_YEAR_RE.finditer(bare):
            name = re.sub(r"^(?:The|Under|Of|In|And|Per|By|From|With|See|As)\s+", "", m.group(1))
            if _act_known(name, None, lib) or name.lower() in user_text.lower() or _is_repealed_name(name, lib) or HONEST_LIMIT.search(bare):
                continue
            report.issues.append(Issue("law_not_in_library", f"Paragraph {i + 1} names '{name}', which is not in the verified library. Delete it, or say plainly that you could not verify it.", i))

        # 5b. Stating law, or that something cannot happen, needs a source or an honest limit.
        if not tokens and not HONEST_LIMIT.search(bare):
            for sentence in re.split(r"(?<=[.!?।])\s+", bare):
                hit = LAW_ASSERTION.search(sentence)
                if hit and hit.group(0).lower() not in user_text.lower():
                    report.issues.append(Issue("uncited_law_statement", f"Paragraph {i + 1} says {sentence.strip()[:120]!r} with no source. Read the law and cite it, or say you could not verify this.", i, quote=sentence))
        if not HONEST_LIMIT.search(bare):
            for sentence in re.split(r"(?<=[.!?।])\s+", bare):
                if NONEXISTENCE.search(sentence):
                    report.issues.append(Issue("claimed_nonexistence", f"Paragraph {i + 1} says {sentence.strip()[:120]!r}. Your library is not all of Indian law, so you can't know that. Say you could not find it in the laws you can verify.", i, quote=sentence))

        # 6. Repealed laws: naming one is fine only when the replacement is cited alongside it.
        for law in lib.repealed_laws:
            pattern = re.compile(r"(?<![\w])(?:" + "|".join(law["aliases"]) + r")(?![\w])", re.I)
            if pattern.search(bare) and not any(t.startswith(law["successor"] + ":") for t in tokens):
                report.issues.append(Issue("repealed_law", f"Paragraph {i + 1} names the {law['name']}, which is no longer in force. {law['note']} Cite the replacement law ({lib.sources[law['successor']]['title']}) in the same paragraph, or remove it.", i))

        # 7. Time limits and money must trace to a source, a tool, or the user.
        for value, unit in durations(bare_ascii):
            if (value, unit) not in allowed_durations:
                report.issues.append(Issue("unsupported_number", f"Paragraph {i + 1} states {value} {unit}(s), which is not in any cited provision or in the user's facts. If the law sets this period, cite it. If it is your own suggestion, do not give any number: say 'a short deadline' instead, or use [NUMBER OF DAYS] in a document.", i))
        for value in money(bare_ascii):
            if value and value not in allowed_money:
                report.issues.append(Issue("unsupported_number", f"Paragraph {i + 1} states the amount {value:,} rupees, which is not in any cited provision or in the user's facts. Check it or remove it.", i))

        # 8. Quoted statute wording must match the text cited in that paragraph.
        for quote in re.findall(r"[\"“]([^\"”]{25,})[\"”]", bare):
            if not tokens or re.search(r"[^\x00-\x7F]{4,}", quote):
                continue  # Translations can't be matched word for word; the audit covers those.
            if squash(quote) in squash(user_text):
                continue
            cited_text = squash(" ".join(lib.full_text(t) for t in tokens if lib.get(t)))
            if squash(quote) not in cited_text:
                report.issues.append(Issue("misquote", f"Paragraph {i + 1} quotes {quote[:70]!r}, which does not match the cited text. Quote exactly, or paraphrase without quote marks.", i))

    # De-duplicate: one wrong number can appear several times.
    unique, seen = [], set()
    for issue in report.issues:
        key = (issue.code, issue.detail)
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    report.issues = unique
    return report


def _mentioned_by_user(num: str, user_ascii: str) -> bool:
    return bool(re.search(rf"(?<![\w]){re.escape(num)}(?![\w])", user_ascii, re.I))


def _act_known(name: str, year: str, lib) -> bool:
    low = re.sub(r"[^a-z0-9 ]", "", name.lower())
    for source in lib.sources.values():
        title = re.sub(r"[^a-z0-9 ]", "", source["title"].lower())
        if (year is None or year in title) and (low in title or title in low or _word_overlap(low, title) >= 0.75):
            return True
    return False


def _word_overlap(a: str, b: str) -> float:
    aw, bw = set(a.split()) - {"the", "of", "and"}, set(b.split()) - {"the", "of", "and"}
    return len(aw & bw) / max(len(aw), 1)


def _is_repealed_name(name: str, lib) -> bool:
    return any(re.search("|".join(law["aliases"]), name, re.I) for law in lib.repealed_laws)


def fact_check_document(body: str, user_text: str, tool_numbers: str = "") -> list[Issue]:
    """A draft going out in the user's name may not contain invented dates or amounts."""
    issues = []
    allowed = to_ascii_digits(user_text + " " + tool_numbers)
    bare = to_ascii_digits(CITE_RE.sub(" ", re.sub(r"\[[A-Z][^\]]*\]", " ", body)))
    for m in re.finditer(r"\b(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})\b|\b\d{1,2}(?:st|nd|rd|th)?\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?,?\s+\d{4}\b|"
                         r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b", bare):
        if not _date_in(m.group(0), allowed):
            issues.append(Issue("invented_fact", f"The document states the date '{m.group(0)}', which the user never gave. Use a [PLACEHOLDER] or ask the user."))
    allowed_money = money(allowed)
    for value in money(bare):
        if value and value not in allowed_money:
            issues.append(Issue("invented_fact", f"The document states the amount {value:,} rupees, which the user never gave. Use a [PLACEHOLDER] or ask the user."))
    return issues


MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _date_key(text: str):
    t = text.lower()
    m = re.match(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", t)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        return d, mo, y + 2000 if y < 100 else y
    m = re.match(r"(\d{1,2})(?:st|nd|rd|th)?\s+([a-z]{3})[a-z]*\.?,?\s+(\d{4})", t)
    if m:
        return int(m.group(1)), MONTHS.get(m.group(2)), int(m.group(3))
    m = re.match(r"([a-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s+(\d{4})", t)
    if m:
        return int(m.group(2)), MONTHS.get(m.group(1)), int(m.group(3))
    return None


def _date_in(date_text: str, haystack: str) -> bool:
    key = _date_key(date_text)
    if key is None:
        return date_text.lower() in haystack.lower()
    pattern = (r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b|\b\d{1,2}(?:st|nd|rd|th)?\s+[A-Za-z]{3}[a-z]*\.?,?\s+\d{4}\b|"
               r"\b[A-Za-z]{3}[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b")
    return any(_date_key(m.group(0)) == key for m in re.finditer(pattern, haystack))


SCRIPTS = [("devanagari", "\u0900", "\u097f"), ("bengali", "\u0980", "\u09ff"), ("gurmukhi", "\u0a00", "\u0a7f"), ("gujarati", "\u0a80", "\u0aff"),
           ("odia", "\u0b00", "\u0b7f"), ("tamil", "\u0b80", "\u0bff"), ("telugu", "\u0c00", "\u0c7f"), ("kannada", "\u0c80", "\u0cff"),
           ("malayalam", "\u0d00", "\u0d7f"), ("arabic", "\u0600", "\u06ff"), ("latin", "A", "z")]
ASKED_FOR_LANGUAGE = re.compile(r"\b(in|into|mein|me)\s+(english|hindi|hinglish|tamil|telugu|bengali|bangla|marathi|gujarati|kannada|malayalam|punjabi|urdu|odia)\b|"
                                r"(हिंदी|हिन्दी|अंग्रेज़ी|अंग्रेजी|इंग्लिश)\s*(में|मे)", re.I)


def main_script(text: str) -> str | None:
    """Which writing system most of this text uses. None if it's too short to tell."""
    counts: dict[str, int] = {}
    for ch in CITE_RE.sub("", text):
        if ch.isalpha():
            for name, low, high in SCRIPTS:
                if low <= ch <= high:
                    counts[name] = counts.get(name, 0) + 1
                    break
    if sum(counts.values()) < 20:
        return None
    return max(counts, key=counts.get)


def language_mismatch(user_message: str, reply: str) -> str | None:
    """Explain the problem if the reply isn't in the script the person wrote in."""
    if ASKED_FOR_LANGUAGE.search(user_message):
        return None  # They asked for a particular language, so their script proves nothing.
    wanted, got = main_script(user_message), main_script(reply)
    if wanted and got and wanted != got:
        return f"The person wrote in {wanted} script but your reply is mostly in {got} script. Write the whole reply again in the language and script of their latest message."
    if wanted == got == "latin":
        # English and Hinglish share an alphabet, so compare the words instead.
        theirs, ours = _hinglish_share(user_message), _hinglish_share(reply)
        if theirs < 0.05 and ours >= 0.12:
            return "The person wrote in English but your reply is in Hinglish. Write the whole reply again in English."
        if theirs >= 0.2 and ours < 0.03:
            return "The person wrote in Hinglish but your reply is in English. Write the whole reply again in Hinglish."
    return None


def _hinglish_share(text: str) -> float:
    words = re.findall(r"[a-z]+", CITE_RE.sub("", text).lower())
    return sum(w in HINGLISH_WORDS for w in words) / len(words) if words else 0.0


HEADING_ONLY = re.compile(r"^(\*\*[^*\n]+\*\*:?|#{1,4} [^\n]+)$")


def _remove_sentence(paragraphs: list[str], quote: str) -> bool:
    """Delete the sentence containing this quote, keeping list lines intact. False if not found."""
    target = squash(CITE_RE.sub("", quote))
    if len(target) < 15:
        return False
    for index, para in enumerate(paragraphs):
        if target not in squash(CITE_RE.sub("", para)):
            continue
        lines, changed = [], False
        for line in para.split("\n"):
            pieces = []
            for sentence in re.split(r"(?<=[.!?।])\s+", line):
                bare = squash(CITE_RE.sub("", sentence))
                if bare and (bare in target or target in bare):
                    changed = True
                else:
                    pieces.append(sentence)
            kept_line = " ".join(pieces)
            if re.search(r"\w", CITE_RE.sub("", kept_line)):
                lines.append(kept_line)
        if changed:
            paragraphs[index] = "\n".join(lines)
            return True
    return False


def strip_failed_paragraphs(text: str, issues: list[Issue]) -> tuple[str, int]:
    """Last resort: delete what could not be verified. The caller tells the user."""
    paragraphs = split_paragraphs(text)
    bad, removed = set(), 0
    for issue in issues:
        if not issue.blocking:
            continue
        # One flagged sentence shouldn't cost the person the whole paragraph around it.
        if issue.quote and _remove_sentence(paragraphs, issue.quote):
            removed += 1
        elif issue.paragraph is not None and issue.paragraph < len(paragraphs) and issue.paragraph not in bad:
            bad.add(issue.paragraph)
            removed += 1
    kept = [p for i, p in enumerate(paragraphs) if i not in bad and re.search(r"\w", CITE_RE.sub("", p))]
    # A heading whose section has gone would just confuse the reader.
    tidy = []
    for i, para in enumerate(kept):
        following = kept[i + 1] if i + 1 < len(kept) else None
        if HEADING_ONLY.match(para.strip()) and (following is None or HEADING_ONLY.match(following.strip())):
            continue
        tidy.append(para)
    return "\n\n".join(tidy), removed


def render_citations(text: str) -> str:
    """Turn [[CPA2019:s35]] into 'Section 35, Consumer Protection Act, 2019' for documents."""
    lib = library()
    return CITE_RE.sub(lambda m: lib.label(m.group(1)), text)
