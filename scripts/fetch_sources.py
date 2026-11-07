"""Download every source in corpus/manifest.json and record provenance.

Each PDF is stored byte-for-byte in corpus/raw/. corpus/provenance.json records
the URL it came from, the retrieval date and a SHA-256.

A file is only accepted if it passes the identity check in the manifest. This
exists because a real failure happened while building this corpus: the "Acts" on
a reputable site turned out to be the *Bills* ("ARRANGEMENT OF CLAUSES"), and
quoting a Bill as if it were enacted law is exactly the error this project exists
to prevent.
"""
import concurrent.futures as cf
import datetime as dt
import hashlib
import json
import pathlib
import re
import subprocess
import sys
import tempfile

from pypdf import PdfReader

ROOT = pathlib.Path(__file__).resolve().parent.parent
RAW = ROOT / "corpus" / "raw"


def identity_problem(path, entry):
    """Return why a PDF is not the document the manifest expects, or None."""
    reader = PdfReader(str(path))
    head = "\n".join((p.extract_text() or "") for p in reader.pages[:25])
    head = re.sub(r"/G(\d+)", lambda m: chr(int(m.group(1))), head)
    for bad in entry.get("forbid", []):
        if re.search(bad, head):
            return f"looks like a Bill or draft (matched {bad!r})"
    want = entry.get("must_contain")
    if want and not re.search(re.escape(want).replace(r"\ ", r"\s+"), head, re.I):
        return f"missing expected marker {want!r}"
    return None


def fetch(entry):
    dest = RAW / f"{entry['id']}.pdf"
    errors = []
    for url in entry["urls"]:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = pathlib.Path(tmp.name)
        # curl rather than requests: several Indian government hosts serve chains
        # that certifi rejects but the OS trust store accepts.
        cmd = ["curl", "-sS", "-L", "-m", "240", "--connect-timeout", "60",
               "-A", "Mozilla/5.0", "-o", str(tmp_path), "-w", "%{http_code}", url]
        run = subprocess.run(cmd, capture_output=True, text=True)
        data = tmp_path.read_bytes() if tmp_path.exists() else b""
        if run.returncode or run.stdout != "200" or data[:4] != b"%PDF":
            errors.append(f"{url}: curl={run.returncode} http={run.stdout}")
            tmp_path.unlink(missing_ok=True)
            continue
        problem = identity_problem(tmp_path, entry)
        if problem:
            errors.append(f"{url}: REJECTED {problem}")
            tmp_path.unlink(missing_ok=True)
            continue
        dest.write_bytes(data)
        tmp_path.unlink(missing_ok=True)
        return entry["id"], {
            "url": url,
            "retrieved": dt.date.today().isoformat(),
            "sha256": hashlib.sha256(data).hexdigest(),
            "bytes": len(data),
            "mirror": bool(entry.get("mirror")),
        }, None
    return entry["id"], None, errors


def main():
    manifest = json.loads((ROOT / "corpus" / "manifest.json").read_text())
    prov_path = ROOT / "corpus" / "provenance.json"
    prov = json.loads(prov_path.read_text()) if prov_path.exists() else {}
    force = set(a for a in sys.argv[1:] if not a.startswith("-"))
    todo = [e for e in manifest
            if e["id"] not in prov or e["id"] in force or "--force" in sys.argv
            or prov[e["id"]].get("url") not in e["urls"]]
    with cf.ThreadPoolExecutor(6) as ex:
        for sid, info, errors in ex.map(fetch, todo):
            if info:
                prov[sid] = info
                print(f"OK   {sid:10} {info['bytes']:>9} bytes  {info['url']}", flush=True)
            else:
                prov.pop(sid, None)
                print(f"FAIL {sid:10} {errors}", flush=True)
    prov_path.write_text(json.dumps(prov, indent=1, sort_keys=True))
    print("missing:", [e["id"] for e in manifest if e["id"] not in prov])


if __name__ == "__main__":
    main()
