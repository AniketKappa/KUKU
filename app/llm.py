"""Choose the AI provider. Claude, Gemini, Grok and DeepSeek all work; the checks stay identical.

The rest of the app keeps one internal message format and never knows which provider
runs. Gemini, Grok and DeepSeek are reached through OpenAI-compatible APIs, and this module
translates requests and replies at the edge. The verifier, audit, triage, tools and legal
library are the same whichever model is used, so a weaker model produces more rejected
drafts, not more invented law reaching the user.
"""
from __future__ import annotations

import base64
import io
import json
import re
import time
import uuid
from types import SimpleNamespace

import anthropic
import openai

from . import config

STATUS_ERRORS = (anthropic.APIStatusError, openai.APIStatusError)
CONNECTION_ERRORS = (anthropic.APIConnectionError, openai.APIConnectionError)
API_ERRORS = (anthropic.APIError, openai.APIError)


class Block(SimpleNamespace):
    """A reply block with the same shape the rest of the app reads."""

    def model_dump(self, **_) -> dict:
        return {k: v for k, v in self.__dict__.items() if v is not None}


def clean_schema(schema, provider: str):
    """Gemini accepts a narrower schema dialect, so convert to what it documents."""
    if provider != "gemini":
        return schema
    if isinstance(schema, list):
        return [clean_schema(s, provider) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {}
    for key, value in schema.items():
        if key == "additionalProperties":
            continue
        if key == "type" and isinstance(value, list):
            kinds = [v for v in value if v != "null"]
            out["type"] = kinds[0] if kinds else "string"
            if "null" in value:
                out["nullable"] = True
        else:
            out[key] = clean_schema(value, provider)
    return out


def _png_if_needed(media_type: str, data: str, provider: str) -> tuple[str, str]:
    """Grok documents JPEG and PNG input only, so convert anything else."""
    if provider != "xai" or media_type in ("image/jpeg", "image/png"):
        return media_type, data
    from PIL import Image
    buffer = io.BytesIO()
    Image.open(io.BytesIO(base64.b64decode(data))).convert("RGB").save(buffer, format="PNG")
    return "image/png", base64.b64encode(buffer.getvalue()).decode()


def _part(block: dict, provider: str) -> dict:
    kind = block.get("type")
    if kind == "text":
        return {"type": "text", "text": block["text"]}
    if kind == "image":
        src = block["source"]
        media, data = _png_if_needed(src["media_type"], src["data"], provider)
        return {"type": "image_url", "image_url": {"url": f"data:{media};base64,{data}"}}
    if kind == "document":
        if provider == "gemini":
            src = block["source"]
            return {"type": "image_url", "image_url": {"url": f"data:{src['media_type']};base64,{src['data']}"}}
        return {"type": "text", "text": "[A scanned PDF was attached, but this AI provider cannot read it. Ask the person to upload a photo of each page instead.]"}
    return {"type": "text", "text": str(block)}


def to_openai_messages(system, messages: list, provider: str) -> list[dict]:
    """Translate the app's message history to the chat-completions format."""
    text = system if isinstance(system, str) else "\n".join(b["text"] for b in (system or []))
    out = [{"role": "system", "content": text}] if text else []
    for message in messages:
        role, content = message["role"], message["content"]
        if role == "system":
            # Compatibility layers vary on mid-conversation system messages, so this is safest.
            out.append({"role": "user", "content": f"[Automatic message from the app, not from the person]\n{content}"})
            continue
        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue
        if role == "assistant":
            texts = [b["text"] for b in content if b.get("type") == "text"]
            thinking = "\n".join(b.get("thinking", "") for b in content if b.get("type") == "thinking")
            calls = []
            for b in content:
                if b.get("type") == "tool_use":
                    call = {"id": b["id"], "type": "function",
                            "function": {"name": b["name"], "arguments": json.dumps(b["input"], ensure_ascii=False)}}
                    # Gemini ties a hidden signature to each tool call and needs it back unchanged.
                    if b.get("extra_content"):
                        call["extra_content"] = b["extra_content"]
                    calls.append(call)
            item = {"role": "assistant", "content": "\n".join(texts) or None}
            if calls:
                item["tool_calls"] = calls
            elif not texts:
                item["content"] = "(no text)"
            # DeepSeek returns a 400 if earlier reasoning is missing from a request with tools.
            if provider == "deepseek":
                item["reasoning_content"] = thinking
            out.append(item)
            continue
        parts = []
        for block in content:
            if block.get("type") == "tool_result":
                out.append({"role": "tool", "tool_call_id": block["tool_use_id"], "content": block["content"]})
            else:
                parts.append(_part(block, provider))
        if parts:
            if all(p["type"] == "text" for p in parts):
                out.append({"role": "user", "content": "\n".join(p["text"] for p in parts)})
            else:
                out.append({"role": "user", "content": parts})
    return out


def to_openai_tools(tools: list, provider: str) -> list[dict]:
    converted = []
    for tool in tools:
        function = {"name": tool["name"], "description": tool["description"],
                    "parameters": clean_schema(tool["input_schema"], provider)}
        if provider == "xai" and tool.get("strict"):
            function["strict"] = True
        converted.append({"type": "function", "function": function})
    return converted


def _json_has_required_keys(response, schema: dict) -> bool:
    try:
        data = json.loads(response.choices[0].message.content)
    except (AttributeError, IndexError, TypeError, ValueError):
        return False
    return isinstance(data, dict) and all(key in data for key in schema.get("required", []))


STOP_REASONS = {"stop": "end_turn", "tool_calls": "tool_use", "function_call": "tool_use", "length": "max_tokens", "content_filter": "refusal"}


def from_openai_response(response) -> SimpleNamespace:
    """Translate a chat-completions reply to the app's internal shape."""
    usage = getattr(response, "usage", None)
    details = getattr(usage, "prompt_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    prompt = getattr(usage, "prompt_tokens", 0) or 0
    mapped_usage = SimpleNamespace(input_tokens=max(prompt - cached, 0), output_tokens=getattr(usage, "completion_tokens", 0) or 0,
                                   cache_read_input_tokens=cached, cache_creation_input_tokens=0)
    if not getattr(response, "choices", None):
        # Some providers block a request by returning no choices at all.
        return SimpleNamespace(content=[], stop_reason="refusal", usage=mapped_usage)
    choice = response.choices[0]
    message = choice.message
    blocks = []
    reasoning = getattr(message, "reasoning_content", None)
    if reasoning:
        blocks.append(Block(type="thinking", thinking=reasoning))
    if message.content and message.content.strip():
        blocks.append(Block(type="text", text=message.content))
    for call in message.tool_calls or []:
        try:
            arguments = json.loads(call.function.arguments or "{}")
        except json.JSONDecodeError:
            arguments = {}  # The tool reports bad input, and the model gets a chance to retry.
        dumped = call.model_dump() if hasattr(call, "model_dump") else {}
        blocks.append(Block(type="tool_use", id=call.id or f"call_{uuid.uuid4().hex[:12]}", name=call.function.name,
                            input=arguments, extra_content=dumped.get("extra_content")))
    stop = STOP_REASONS.get(choice.finish_reason, "end_turn")
    if any(b.type == "tool_use" for b in blocks) and stop != "max_tokens":
        stop = "tool_use"
    return SimpleNamespace(content=blocks, stop_reason=stop, usage=mapped_usage)


class OpenAICompatClient:
    """Offers the same `.messages.create` and `.beta.messages.create` calls as the Claude SDK."""

    def __init__(self, provider: str, sdk_client=None):
        self.provider = provider
        # Retries are handled in _send, which can wait as long as a rate limit needs.
        self.sdk = sdk_client or openai.OpenAI(api_key=config.api_key(provider), base_url=config.BASE_URLS[provider],
                                               max_retries=0, timeout=150.0)
        self.messages = SimpleNamespace(create=self._create)
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, model, max_tokens, messages, system=None, tools=None, output_config=None, **_ignored):
        # Claude-only options (thinking, betas, fallbacks, caching) arrive in _ignored.
        output_config = output_config or {}
        schema = (output_config.get("format") or {}).get("schema")
        if schema and self.provider == "deepseek":
            # DeepSeek's JSON mode doesn't enforce a schema, so it goes in the prompt instead.
            text = system if isinstance(system, str) else "\n".join(b["text"] for b in (system or []))
            system = (f"{text}\n\nOutput only a valid JSON object and no other text. It must follow this JSON schema "
                      f"and include every required key, using an empty list where nothing applies:\n{json.dumps(schema)}")
        kwargs = {"model": model, "max_tokens": max_tokens, "messages": to_openai_messages(system, messages, self.provider)}
        if output_config.get("effort"):
            kwargs["reasoning_effort"] = output_config["effort"]
        if tools:
            kwargs["tools"] = to_openai_tools(tools, self.provider)
            kwargs["tool_choice"] = "auto"
        if schema and self.provider == "deepseek":
            kwargs["response_format"] = {"type": "json_object"}
            return self._send_json(kwargs, schema)
        if schema:
            kwargs["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "result", "schema": clean_schema(schema, self.provider)}}
        return from_openai_response(self._send(kwargs))

    def _send_json(self, kwargs: dict, schema: dict):
        """Retry once if the JSON is blank or incomplete. Its docs warn content can come back blank.

        A reply that's still malformed is returned as it is, so the audit fails and the app
        reports that the check didn't run. It must never count as a pass.
        """
        response = None
        for _ in range(2):
            response = self._send(kwargs)
            if _json_has_required_keys(response, schema):
                break
        return from_openai_response(response)

    def _send(self, kwargs: dict):
        """Wait out per-minute rate limits and brief overloads, which are normal on free tiers."""
        for attempt in range(config.RATE_LIMIT_RETRIES + 1):
            try:
                return self.sdk.chat.completions.create(**kwargs)
            except openai.RateLimitError as exc:
                # The useful detail is in the response body, not the exception's own message.
                text = f"{exc} {getattr(getattr(exc, 'response', None), 'text', '')}"
                # Waiting a minute can't fix a daily quota, so report it straight away.
                if re.search(r"per ?day|daily", text, re.I) or attempt == config.RATE_LIMIT_RETRIES:
                    raise
                match = re.search(r"retry (?:in|after)\s+([\d.]+)\s*s|retryDelay['\"]?:\s*['\"]?([\d.]+)s", text, re.I)
                wait = float(match.group(1) or match.group(2)) + 1 if match else 15.0 * (attempt + 1)
                time.sleep(min(wait, 90.0))
            except openai.InternalServerError:
                if attempt == config.RATE_LIMIT_RETRIES:
                    raise
                time.sleep(10.0 * (attempt + 1))
        raise RuntimeError("unreachable")


def make_client():
    if config.PROVIDER == "anthropic":
        return anthropic.Anthropic(max_retries=3, timeout=300.0)
    return OpenAICompatClient(config.PROVIDER)
