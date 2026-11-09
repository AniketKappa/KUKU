"""Web server. Run with: .venv/bin/python -m uvicorn app.server:app --port 8000"""
from __future__ import annotations

import collections
import json
import pathlib
import re
import threading
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response, StreamingResponse
from pydantic import BaseModel

from . import agent, config, documents
from .corpus import library
from .retrieval import index

app = FastAPI(title="Haqdaar")
WEB = pathlib.Path(__file__).resolve().parent.parent / "web"
SESSIONS: dict[str, dict] = {}
MAX_UPLOAD_B64 = 14_000_000
ALLOWED_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/webp", "text/plain"}


class FileIn(BaseModel):
    name: str
    media_type: str
    data: str


class ChatIn(BaseModel):
    session_id: str | None = None
    message: str
    language: str | None = None
    files: list[FileIn] = []


class PracticeStart(BaseModel):
    session_id: str
    role: str
    style: str = "tough"


class PracticeTurn(BaseModel):
    session_id: str
    message: str


def _path(session_id: str) -> pathlib.Path:
    if not re.fullmatch(r"[a-f0-9]{12}", session_id):
        raise HTTPException(400, "Bad session id")
    return config.SESSIONS / f"{session_id}.json"


def _load(session_id: str) -> dict:
    if session_id in SESSIONS:
        return SESSIONS[session_id]
    path = _path(session_id)
    if not path.exists():
        raise HTTPException(404, "Session not found")
    SESSIONS[session_id] = json.loads(path.read_text())
    return SESSIONS[session_id]


def _save(session: dict) -> None:
    _path(session["id"]).write_text(json.dumps(session, ensure_ascii=False))


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@app.on_event("startup")
def warm_up() -> None:
    library()
    index()


@app.get("/")
def home():
    return FileResponse(WEB / "index.html")


@app.get("/api/status")
def status():
    lib = library()
    sources = []
    for source in lib.manifest:
        count = sum(1 for r in lib.records.values() if r["source_id"] == source["id"])
        any_rec = next((r for r in lib.records.values() if r["source_id"] == source["id"]), None)
        sources.append({"title": source["title"], "provisions": count, "jurisdiction": source["jurisdiction"],
                        "status": source["status"], "url": any_rec["url"] if any_rec else None,
                        "retrieved": any_rec["retrieved"] if any_rec else None})
    return {"api_ready": config.api_ready(), "provider": config.PROVIDER, "model": config.MODEL, "provisions": len(lib.records), "sources": sources,
            "semantic_search": index().vectors is not None, "public_demo": config.PUBLIC_DEMO}


_recent: dict[str, collections.deque] = collections.defaultdict(collections.deque)
_recent_lock = threading.Lock()


def _check_rate(request: Request) -> None:
    """A public demo runs on a free quota, so one visitor mustn't use it all up."""
    if config.QUESTIONS_PER_HOUR <= 0:
        return
    forwarded = request.headers.get("x-forwarded-for", "")
    visitor = forwarded.split(",")[0].strip() or (request.client.host if request.client else "unknown")
    now = time.time()
    with _recent_lock:
        times = _recent[visitor]
        while times and now - times[0] > 3600:
            times.popleft()
        if len(times) >= config.QUESTIONS_PER_HOUR:
            raise HTTPException(429, f"This public demo allows {config.QUESTIONS_PER_HOUR} questions an hour per visitor, so the free AI quota lasts for everyone. Please try again later.")
        times.append(now)


@app.get("/api/citation/{pid:path}")
def citation(pid: str):
    record = library().public_record(pid)
    if not record:
        raise HTTPException(404, "No such provision")
    return record


@app.post("/api/chat")
def chat(body: ChatIn, request: Request):
    _check_rate(request)
    if not config.api_ready():
        raise HTTPException(503, "No API key. Add DEEPSEEK_API_KEY, GEMINI_API_KEY, XAI_API_KEY or ANTHROPIC_API_KEY to the .env file and restart.")
    for f in body.files:
        if f.media_type not in ALLOWED_TYPES:
            raise HTTPException(415, "Please upload a PDF, a photo (JPG, PNG, WebP) or a text file.")
        if len(f.data) > MAX_UPLOAD_B64:
            raise HTTPException(413, "That file is too large. Please upload one under 10 MB.")
    session = _load(body.session_id) if body.session_id else agent.new_session()
    SESSIONS[session["id"]] = session

    def stream():
        yield _sse({"type": "session", "session_id": session["id"]})
        try:
            client = agent.make_client()
            files = [f.model_dump() for f in body.files]
            for event in agent.run_turn(client, session, body.message, files=files, language=body.language):
                yield _sse(event)
        except Exception as exc:  # A failure must reach the person, not hang their screen.
            yield _sse({"type": "error", "message": "Something went wrong on our side. Please try again."})
            session["log"].append({"server_error": f"{type(exc).__name__}: {exc}"[:500]})
        finally:
            _save(session)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/api/practice/start")
def practice_start(body: PracticeStart):
    session = _load(body.session_id)
    agent.practice_start(session, body.role.strip()[:80], body.style)
    _save(session)
    return {"ok": True}


@app.post("/api/practice/say")
def practice_say(body: PracticeTurn, request: Request):
    _check_rate(request)
    session = _load(body.session_id)
    if not session.get("practice"):
        raise HTTPException(400, "No practice session is running")
    reply = agent.practice_turn(agent.make_client(), session, body.message)
    _save(session)
    return {"reply": reply}


@app.post("/api/practice/debrief")
def practice_debrief(body: PracticeTurn):
    session = _load(body.session_id)
    if not session.get("practice"):
        raise HTTPException(400, "No practice session is running")

    def stream():
        try:
            for event in agent.practice_debrief(agent.make_client(), session):
                yield _sse(event)
        except Exception as exc:
            yield _sse({"type": "error", "message": "Something went wrong on our side. Please try again."})
            session["log"].append({"server_error": f"{type(exc).__name__}: {exc}"[:500]})
        finally:
            _save(session)

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/session/{session_id}/document/{doc_id}.docx")
def download(session_id: str, doc_id: str):
    session = _load(session_id)
    doc = next((d for d in session["documents"] if d["id"] == doc_id), None)
    if not doc:
        raise HTTPException(404, "Document not found")
    name = re.sub(r"[^A-Za-z0-9]+", "-", doc["title"]).strip("-")[:60] or "document"
    return Response(documents.to_docx(doc), media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    headers={"Content-Disposition": f'attachment; filename="{name}.docx"'})


@app.get("/api/session/{session_id}")
def restore(session_id: str):
    """Bring a conversation back after a page refresh."""
    session = _load(session_id)
    turns = []
    for entry in session["log"]:
        if entry.get("final"):
            turns.append({"user": entry.get("user"), "text": entry["final"], "citations": agent.citations_payload(entry["final"]),
                          "verification": {"status": entry.get("final_status"), "repairs": entry.get("repairs", 0), "removed": 0, "issues": []}})
    lib = library()
    triage = session["triage"]
    return {"turns": turns, "case_file": session["case_file"], "assessment": session["assessment"], "documents": session["documents"],
            "triage": {**triage, "help_notes": [lib.public_record(n) for n in triage["help"] if lib.get(n)]}}


@app.delete("/api/session/{session_id}")
def forget(session_id: str):
    """Let a person erase their case from this machine."""
    SESSIONS.pop(session_id, None)
    _path(session_id).unlink(missing_ok=True)
    return {"ok": True}
