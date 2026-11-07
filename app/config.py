"""Central settings. Everything here can be overridden from the environment or .env."""
import os
import pathlib

from dotenv import load_dotenv

ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

KEY_NAMES = {
    "anthropic": ["ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"],
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "xai": ["XAI_API_KEY", "GROK_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
}
BASE_URLS = {
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
    "xai": "https://api.x.ai/v1",
    "deepseek": "https://api.deepseek.com",
}
DEFAULT_MODELS = {"anthropic": "claude-opus-5-5", "gemini": "gemini-3.6-flash", "xai": "grok-4.7", "deepseek": "deepseek-flash"}
# USD per million tokens, list prices checked on 2026-11-07. Gemini's free tier costs nothing.
PRICES = {
    "claude-opus-5-5": {"input": 4.0, "output": 20.0, "cache_read": 0.2, "cache_write": 5.0},
    "gemini-3.6-flash": {"input": 0.75, "output": 3.75, "cache_read": 0.75, "cache_write": 0.0},
    "gemini-3.8-flash": {"input": 0.75, "output": 3.75, "cache_read": 0.75, "cache_write": 0.0},
    "grok-4.7": {"input": 2.0, "output": 6.0, "cache_read": 0.5, "cache_write": 0.0},
    # DeepSeek's peak rates, so the cost shown is an upper limit. Off-peak is half this.
    "deepseek-flash": {"input": 0.3, "output": 1.2, "cache_read": 0.006, "cache_write": 0.0},
    "deepseek-v4-pro": {"input": 1.32, "output": 3.96, "cache_read": 0.044, "cache_write": 0.0},
}


def api_key(provider: str) -> str | None:
    for name in KEY_NAMES[provider]:
        if os.getenv(name):
            return os.getenv(name)
    return None


def _choose_provider() -> str:
    """Use LLM_PROVIDER if set, otherwise whichever key is present."""
    chosen = os.getenv("LLM_PROVIDER", "").strip().lower()
    aliases = {"claude": "anthropic", "google": "gemini", "grok": "xai"}
    chosen = aliases.get(chosen, chosen)
    if chosen in KEY_NAMES:
        return chosen
    for provider in KEY_NAMES:
        if api_key(provider):
            return provider
    return "anthropic"


PROVIDER = _choose_provider()
MODEL = os.getenv("LAWYER_MODEL", DEFAULT_MODELS[PROVIDER])
AUDIT_MODEL = os.getenv("LAWYER_AUDIT_MODEL", MODEL)
EFFORT = os.getenv("LAWYER_EFFORT", "medium")
AUDIT_EFFORT = os.getenv("LAWYER_AUDIT_EFFORT", "low")
MAX_TOKENS = int(os.getenv("LAWYER_MAX_TOKENS", "16000"))
MAX_TOOL_ROUNDS = int(os.getenv("LAWYER_MAX_TOOL_ROUNDS", "12"))
MAX_REPAIRS = int(os.getenv("LAWYER_MAX_REPAIRS", "3"))
RATE_LIMIT_RETRIES = int(os.getenv("LAWYER_RATE_LIMIT_RETRIES", "3"))
# Claude only: re-runs a request on another model if a safety classifier declines it.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Free tiers have low rate limits, so run scenarios one at a time unless told otherwise.
SCENARIO_WORKERS = int(os.getenv("SCENARIO_WORKERS", "1" if PROVIDER == "gemini" else "4"))

CORPUS = ROOT / "corpus"
DATA = ROOT / "data"
SESSIONS = DATA / "sessions"
DOCS_OUT = DATA / "documents"
EMBED_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

for path in (SESSIONS, DOCS_OUT):
    path.mkdir(parents=True, exist_ok=True)


def api_ready() -> bool:
    return bool(api_key(PROVIDER))
