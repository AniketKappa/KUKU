"""Decide, in plain code, when someone needs a real lawyer or emergency help.

This is deliberately not left to the model. A person who says they were arrested or
are being beaten must get the right numbers even if the model misjudges the situation.
The model's assessment can raise the urgency, but it can never lower what this finds.
"""
from __future__ import annotations

import re

LEVELS = ["none", "recommended", "urgent", "emergency"]

RULES = [
    ("emergency", "physical_danger",
     r"\b(beat(s|ing|en)?|hit(s|ting)? me|hits? me|attack(ed|ing)?|threat(en)?(ed|s|ing)? to (kill|hurt|harm|beat)|kill me|domestic violence|"
     r"rape[d]?|sexual(ly)? assault(ed)?|locked (me )?(in|up)|maar(ta|te|ti)|peet(ta|te|ti)|jaan se maar)\b|"
     r"मारता|मारते|मारती|पीटता|पीटते|पीटती|जान से मार|घरेलू हिंसा|बलात्कार|यौन उत्पीड़न"),
    ("emergency", "self_harm",
     r"\b(kill myself|suicide|end my life|want to die|no reason to live|khud?kushi|jaan de (dunga|dungi))\b|आत्महत्या|खुदकुशी|जान दे दूं|मरना चाहता|मरना चाहती"),
    ("urgent", "arrest_or_police",
     r"\b(arrest(ed)?|police (custody|station|came|called|are|took|took me|picked)|in custody|lock-?up|anticipatory bail|bail|fir (against|on|lodged|filed|registered)|"
     r"giraftar|hirasat|thane? (mein|me|bulaya))\b|गिरफ्तार|हिरासत|थाने|अग्रिम जमानत|जमानत|एफआईआर|प्राथमिकी"),
    ("urgent", "court_process",
     r"\b(summons|warrant|court (date|hearing|notice|order)|ex[- ]parte|decree|hearing (on|is|date)|next hearing|contempt|attachment order|"
     r"appear(ance)? before (the )?(court|commission|magistrate)|samman|peshi)\b|समन|वारंट|अदालत में पेश|कोर्ट की तारीख|पेशी|कुर्की"),
    ("urgent", "losing_home",
     r"\b(evict(ed|ing|ion)?|throw(n|ing)? (me |us )?out|locks? changed|changed the locks?|thrown out|bailiff|nikal (diya|rahe|denge)|ghar se nikal)\b|बेदखल|घर से निकाल|ताला बदल"),
    ("urgent", "cheque_case_against_user",
     r"\b(138 notice|section\s*138|negotiable instruments act|cheque (bounce|dishonou?r)[^.]{0,60}(notice|against me|sent me|received)|notice[^.]{0,60}cheque|"
     r"(initiate|start|file|take)\s+criminal (proceedings|action|case))\b|चेक बाउंस[^।]{0,60}नोटिस|आपराधिक (कार्यवाही|मामला)"),
    ("recommended", "property_title_or_large_value",
     r"\b(crores?|title deed|sale deed|ancestral property|partition|land grab|encroach(ed|ment)?|will dispute|inherit(ance)?)\b|करोड़|पैतृक संपत्ति|बंटवारा|ज़मीन पर कब्जा"),
    ("recommended", "family_law",
     r"\b(divorce|custody of|child custody|maintenance|alimony|talaq|dowry|498a)\b|तलाक|भरण-पोषण|दहेज|बच्चे की कस्टडी"),
    ("recommended", "government_or_tax",
     r"\b(income tax notice|gst notice|enforcement directorate|cbi|tax demand)\b"),
]
COMPILED = [(level, code, re.compile(pattern, re.I)) for level, code, pattern in RULES]

# Each piece of guidance points only at practical notes verified against official pages.
HELP = {
    "physical_danger": ["NOTE:EMERGENCY", "NOTE:WOMEN", "NOTE:NALSA"],
    "self_harm": ["NOTE:EMERGENCY"],
    "arrest_or_police": ["NOTE:NALSA"],
    "court_process": ["NOTE:NALSA"],
    "losing_home": ["NOTE:NALSA"],
    "cheque_case_against_user": ["NOTE:NALSA"],
    "property_title_or_large_value": ["NOTE:NALSA"],
    "family_law": ["NOTE:NALSA"],
    "government_or_tax": ["NOTE:NALSA"],
}
REASONS = {
    "physical_danger": "You may be in physical danger. Your safety comes before any legal step.",
    "self_harm": "What you wrote worries me. Please talk to someone right now. Your safety matters more than this case.",
    "arrest_or_police": "Police involvement or arrest can move fast and affects your liberty. Please get a lawyer today.",
    "court_process": "A court process or date appears to be running. Missing it can cost you the case, so please show this to a lawyer now.",
    "losing_home": "You may be losing your home. Please speak to a lawyer or legal aid today.",
    "cheque_case_against_user": "A cheque-bounce notice has short legal deadlines and can lead to a criminal case. Please show it to a lawyer quickly.",
    "property_title_or_large_value": "Large-value or property title disputes need a lawyer who can read your documents.",
    "family_law": "Family law depends on personal law and your specific facts, and it is not in my verified library. Please speak to a lawyer.",
    "government_or_tax": "Government and tax notices have strict processes that are not in my verified library. Please speak to a lawyer or tax professional.",
}
REASONS_HI = {
    "physical_danger": "आप शारीरिक खतरे में हो सकते हैं। किसी भी कानूनी कदम से पहले आपकी सुरक्षा ज़रूरी है।",
    "self_harm": "आपने जो लिखा उससे मुझे चिंता हो रही है। कृपया अभी किसी से बात करें। इस मामले से ज़्यादा आपकी सुरक्षा मायने रखती है।",
    "arrest_or_police": "पुलिस कार्रवाई या गिरफ्तारी में चीज़ें तेज़ी से होती हैं और आपकी आज़ादी पर असर पड़ता है। कृपया आज ही वकील से मिलें।",
    "court_process": "लगता है अदालती प्रक्रिया या तारीख चल रही है। तारीख छूटने से केस हाथ से जा सकता है, इसलिए अभी वकील को दिखाएं।",
    "losing_home": "आपका घर छिन सकता है। कृपया आज ही वकील या कानूनी सहायता से बात करें।",
    "cheque_case_against_user": "चेक बाउंस नोटिस की समय सीमा कम होती है और आपराधिक मामला बन सकता है। कृपया इसे जल्दी वकील को दिखाएं।",
    "property_title_or_large_value": "बड़ी रकम या संपत्ति के मालिकाना हक के मामलों में ऐसे वकील की ज़रूरत होती है जो आपके दस्तावेज़ पढ़ सके।",
    "family_law": "पारिवारिक कानून आपके निजी कानून और तथ्यों पर निर्भर है, और यह मेरी सत्यापित लाइब्रेरी में नहीं है। कृपया वकील से बात करें।",
    "government_or_tax": "सरकारी और टैक्स नोटिस की प्रक्रिया सख्त होती है और यह मेरी सत्यापित लाइब्रेरी में नहीं है। कृपया वकील या टैक्स विशेषज्ञ से बात करें।",
}


def assess(text: str) -> dict:
    """Return the highest level triggered, why, and which help to show."""
    hits = []
    for level, code, pattern in COMPILED:
        if pattern.search(text or ""):
            hits.append((level, code))
    if not hits:
        return {"level": "none", "codes": [], "reasons": [], "reasons_hi": [], "help": []}
    top = max(hits, key=lambda h: LEVELS.index(h[0]))[0]
    codes = [c for _, c in hits]
    help_ids = []
    for code in codes:
        for nid in HELP.get(code, []):
            if nid not in help_ids:
                help_ids.append(nid)
    return {"level": top, "codes": codes, "reasons": [REASONS[c] for c in codes],
            "reasons_hi": [REASONS_HI[c] for c in codes], "help": help_ids}


def stronger(a: str, b: str) -> str:
    """The higher of two levels. Used so the model can never lower what code found."""
    return a if LEVELS.index(a) >= LEVELS.index(b) else b
