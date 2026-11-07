"""Check each practical note against a saved copy of its official source.

A note is only marked verified when the page actually contains the required text.
Snapshots go in corpus/practical_snapshots/, and unverified notes are never served.
"""
import datetime as dt
import hashlib
import json
import pathlib
import re
import subprocess
import tempfile

from pypdf import PdfReader

ROOT = pathlib.Path(__file__).resolve().parent.parent
SNAP = ROOT / "corpus" / "practical_snapshots"
SNAP.mkdir(exist_ok=True)


def fetch_text(url, slug):
    with tempfile.NamedTemporaryFile(delete=False) as tmp:
        path = pathlib.Path(tmp.name)
    run = subprocess.run(["curl", "-sS", "-L", "-m", "60", "--connect-timeout", "25", "-A", "Mozilla/5.0",
                          "-o", str(path), "-w", "%{http_code}", url], capture_output=True, text=True)
    data = path.read_bytes() if path.exists() else b""
    path.unlink(missing_ok=True)
    if run.stdout != "200" or not data:
        return None
    if data[:4] == b"%PDF":
        pdf = SNAP / f"{slug}.pdf"
        pdf.write_bytes(data)
        text = "\n".join((p.extract_text() or "") for p in PdfReader(str(pdf)).pages)
    else:
        text = re.sub(r"<(script|style)[\s\S]*?</\1>", " ", data.decode("utf-8", "ignore"), flags=re.I)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text))
    (SNAP / f"{slug}.txt").write_text(text)
    return text


def main():
    path = ROOT / "corpus" / "practical_notes.json"
    notes = json.loads(path.read_text())
    for n in notes:
        slug = n["id"].split(":")[1].lower()
        snap = SNAP / f"{slug}.txt"
        text = fetch_text(n["source_url"], slug)
        if text is None and snap.exists():
            text = snap.read_text()
        ok = bool(text) and all(m.lower() in text.lower() for m in n["must_appear"])
        n["verified"] = ok
        if ok:
            n["checked_on"] = dt.date.today().isoformat()
            n["snapshot_sha256"] = hashlib.sha256(text.encode()).hexdigest()
        print(f"{'OK  ' if ok else 'FAIL'} {n['id']:18} {n['source_url']}  text_len={len(text or '')}")
    path.write_text(json.dumps(notes, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main()
