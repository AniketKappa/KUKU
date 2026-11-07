"""Report text-layer quality for each downloaded PDF."""
import pathlib, re, sys
from pypdf import PdfReader
ROOT = pathlib.Path(__file__).resolve().parent.parent
for p in sorted((ROOT / "corpus" / "raw").glob("*.pdf")):
    try:
        r = PdfReader(str(p))
        pages = [(pg.extract_text() or "") for pg in r.pages]
    except Exception as e:
        print(f"{p.stem:10} ERROR {type(e).__name__}: {e}")
        continue
    text = "\n".join(pages)
    latin = sum(c.isascii() and c.isalpha() for c in text)
    deva = sum("ऀ" <= c <= "ॿ" for c in text)
    secs = len(re.findall(r"(?m)^\s*\d+[A-Z]*\.\s+[A-Z]", text))
    empty = sum(1 for t in pages if len(t.strip()) < 40)
    print(f"{p.stem:10} pages={len(pages):4} empty={empty:4} chars={len(text):8} latin={latin:8} deva={deva:7} sec_like={secs:5}")
    if "--sample" in sys.argv:
        print(repr(text[3000:3500]))
