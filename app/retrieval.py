"""Hybrid search over the library: BM25 for exact legal words, embeddings for meaning.

Long provisions are split into overlapping windows so that, for example, a single
definition inside a huge definitions section can still be found. A hit always points
back to its whole provision.

Run `python -m app.retrieval --build` once to create the embedding cache. Without the
cache, search still works using BM25 only.
"""
from __future__ import annotations

import json
import re
import sys
from functools import lru_cache

import numpy as np
from rank_bm25 import BM25Okapi

from . import config
from .corpus import library

WINDOW = 1100
OVERLAP = 200
RRF_K = 60
VEC_PATH = config.CORPUS / "embeddings.npy"
# Laws that only apply in one State. Other States' laws are filtered out of results.
STATE_SOURCES = {"DRC1958": "delhi", "KRA1999": "karnataka", "MRC1999": "maharashtra", "TNRRA2017": "tamil nadu"}
STATE_ALIASES = {"bengaluru": "karnataka", "bangalore": "karnataka", "mysuru": "karnataka", "mumbai": "maharashtra",
                 "pune": "maharashtra", "nagpur": "maharashtra", "chennai": "tamil nadu", "coimbatore": "tamil nadu",
                 "madurai": "tamil nadu", "new delhi": "delhi", "nct of delhi": "delhi", "tn": "tamil nadu"}


@lru_cache(maxsize=1)
def _topics() -> dict:
    data = json.loads((config.CORPUS / "topics.json").read_text())
    data["compiled"] = [(re.compile(t["triggers"], re.I), t) for t in data["topics"]]
    return data


def expand_query(query: str) -> str:
    """People say 'salary' and 'bounce'; statutes say 'wages' and 'dishonour'."""
    low = query.lower()
    extra = [terms for phrase, terms in _topics()["glossary"].items() if phrase in low]
    return f"{query} {' '.join(extra)}" if extra else query


def topic_provisions(expanded_query: str) -> list[str]:
    """Where to start reading for this kind of problem, best-matching topic first."""
    matched = []
    for pattern, topic in _topics()["compiled"]:
        count = len(pattern.findall(expanded_query))
        if count:
            matched.append((count, topic))
    matched.sort(key=lambda m: -m[0])
    out = []
    for _, topic in matched:
        for pid in topic["provisions"]:
            if pid not in out and not pid.startswith("NOTE:"):
                out.append(pid)
    return out


def tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def normalise_state(state: str | None) -> str | None:
    if not state:
        return None
    key = state.strip().lower()
    if key in ("", "unknown", "not known", "not stated", "not specified", "n/a", "na", "none", "null", "india", "unclear"):
        return None
    # The model may write "Tamil Nadu (Chennai)" or "Koramangala, Bangalore", so look inside.
    for name in set(STATE_SOURCES.values()):
        if name in key:
            return name
    for alias, name in STATE_ALIASES.items():
        if re.search(rf"\b{re.escape(alias)}\b", key):
            return name
    return key


def _windows(rec: dict) -> list[str]:
    head = f"{rec['act_short']} {rec['unit']} {rec['number']}: {rec['heading']}\n"
    text = rec["text"]
    if len(text) <= WINDOW:
        return [head + text]
    step = WINDOW - OVERLAP
    return [head + text[i:i + WINDOW] for i in range(0, max(len(text) - OVERLAP, 1), step)]


class Index:
    def __init__(self):
        lib = library()
        self.owner: list[str] = []
        self.chunks: list[str] = []
        for pid, rec in lib.records.items():
            if rec.get("repealed"):
                continue  # A stub saying "[Repealed]" must never be offered as law.
            for chunk in _windows(rec):
                self.owner.append(pid)
                self.chunks.append(chunk)
        self.bm25 = BM25Okapi([tokenize(c) for c in self.chunks])
        self.vectors = None
        self.embedder = None
        if config.SEMANTIC_SEARCH and VEC_PATH.exists():
            vectors = np.load(VEC_PATH)
            if len(vectors) == len(self.chunks):
                self.vectors = vectors

    def _embed_query(self, query: str) -> np.ndarray:
        if self.embedder is None:
            from fastembed import TextEmbedding
            self.embedder = TextEmbedding(config.EMBED_MODEL)
        vec = next(iter(self.embedder.embed([query])))
        return vec / (np.linalg.norm(vec) or 1)

    def rank(self, query: str, mode: str = "hybrid") -> list[int]:
        """Chunk indices, best first."""
        rankings = []
        expanded = expand_query(query)
        if mode in ("hybrid", "bm25"):
            scores = self.bm25.get_scores(tokenize(expanded))
            rankings.append(np.argsort(-scores)[:200])
        if mode in ("hybrid", "dense") and self.vectors is not None:
            scores = self.vectors @ self._embed_query(expanded)
            rankings.append(np.argsort(-scores)[:200])
        if mode == "hybrid":
            first_chunk = {}
            for i, owner in enumerate(self.owner):
                first_chunk.setdefault(owner, i)
            # Within a topic, let the query's own words decide the order.
            topic_ids = topic_provisions(expanded)
            scores = self.bm25.get_scores(tokenize(expanded))
            ordered = sorted((first_chunk[p] for p in topic_ids if p in first_chunk), key=lambda i: -scores[i])
            if ordered:
                rankings.append(ordered)
        if len(rankings) == 1:
            return [int(i) for i in rankings[0]]
        fused: dict[int, float] = {}
        for ranking in rankings:
            for pos, idx in enumerate(ranking):
                fused[int(idx)] = fused.get(int(idx), 0.0) + 1.0 / (RRF_K + pos)
        return sorted(fused, key=fused.get, reverse=True)

    def search(self, query: str, state: str | None = None, k: int = 6, mode: str = "hybrid") -> list[dict]:
        lib = library()
        state = normalise_state(state)
        seen, hits = set(), []
        for idx in self.rank(query, mode):
            pid = self.owner[idx]
            if pid in seen:
                continue
            rec = lib.records[pid]
            own_state = STATE_SOURCES.get(rec["source_id"])
            # Unknown State: leave State laws in, since the model is told to ask.
            if own_state and state and own_state != state:
                continue
            seen.add(pid)
            hits.append({
                "id": pid,
                "citation": lib.label(pid),
                "heading": rec["heading"],
                "excerpt": self.chunks[idx].split("\n", 1)[-1][:900],
                "applies_in": rec["jurisdiction"],
                "caution": lib.caution(pid),
            })
            if len(hits) >= k:
                break
        return hits


@lru_cache(maxsize=1)
def index() -> Index:
    return Index()


def build_embeddings():
    from fastembed import TextEmbedding
    idx = index()
    print(f"embedding {len(idx.chunks)} chunks with {config.EMBED_MODEL}", flush=True)
    model = TextEmbedding(config.EMBED_MODEL)
    vectors = np.array(list(model.embed(idx.chunks, batch_size=32)), dtype=np.float32)
    vectors /= np.clip(np.linalg.norm(vectors, axis=1, keepdims=True), 1e-9, None)
    np.save(VEC_PATH, vectors)
    print("saved", VEC_PATH, vectors.shape, flush=True)


if __name__ == "__main__":
    if "--build" in sys.argv:
        build_embeddings()
