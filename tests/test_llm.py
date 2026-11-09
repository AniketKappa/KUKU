"""Check the Gemini and Grok translation layer offline, with a fake API.

These prove the translation is right against the formats in each provider's docs. They
cannot prove the providers accept every request. scripts/smoke_test.py checks that live.
"""
import datetime as dt
import json
from types import SimpleNamespace as NS

import pytest

from app import agent, audit, llm
from app.tools import TOOLS

TODAY = dt.date(2026, 11, 7)


def api_reply(content=None, tool_calls=None, finish="stop", prompt=100, completion=20, cached=30):
    message = NS(content=content, tool_calls=tool_calls)
    usage = NS(prompt_tokens=prompt, completion_tokens=completion, prompt_tokens_details=NS(cached_tokens=cached))
    return NS(choices=[NS(message=message, finish_reason=finish)], usage=usage)


def tool_call(name, args, tid="call_1", extra=None):
    call = NS(id=tid, function=NS(name=name, arguments=json.dumps(args)))
    call.model_dump = lambda: {"id": tid, "extra_content": extra}
    return call


class FakeSDK:
    def __init__(self, replies):
        self.replies = list(replies)
        self.requests = []
        self.chat = NS(completions=NS(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        return self.replies.pop(0)


def client_with(replies, provider="gemini"):
    sdk = FakeSDK(replies)
    return llm.OpenAICompatClient(provider, sdk_client=sdk), sdk


def test_request_drops_claude_only_options_and_maps_effort():
    client, sdk = client_with([api_reply("hi")])
    client.beta.messages.create(model="m", max_tokens=10, betas=["x"], fallbacks="default", thinking={"type": "adaptive"},
                                output_config={"effort": "low"}, system=[{"type": "text", "text": "S", "cache_control": {}}],
                                messages=[{"role": "user", "content": "hello"}])
    sent = sdk.requests[0]
    assert sent["reasoning_effort"] == "low"
    assert not {"betas", "fallbacks", "thinking", "output_config"} & set(sent)
    assert sent["messages"][0] == {"role": "system", "content": "S"}


def test_mid_conversation_system_message_becomes_labelled_user_message():
    messages = llm.to_openai_messages("S", [{"role": "user", "content": "hi"}, {"role": "system", "content": "Today is X"}], "gemini")
    assert messages[-1]["role"] == "user" and "not from the person" in messages[-1]["content"] and "Today is X" in messages[-1]["content"]


def test_tool_results_become_tool_messages_with_matching_ids():
    history = [{"role": "assistant", "content": [{"type": "tool_use", "id": "a", "name": "search_law", "input": {"query": "q"}}]},
               {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "found"}]}]
    out = llm.to_openai_messages(None, history, "xai")
    assert out[0]["tool_calls"][0]["id"] == "a"
    assert json.loads(out[0]["tool_calls"][0]["function"]["arguments"]) == {"query": "q"}
    assert out[1] == {"role": "tool", "tool_call_id": "a", "content": "found"}


def test_gemini_hidden_signature_survives_the_round_trip():
    response = llm.from_openai_response(api_reply(tool_calls=[tool_call("get_provision", {"id": "x"}, extra={"google": {"thought_signature": "sig"}})], finish="tool_calls"))
    stored = json.loads(json.dumps(agent.block_to_dict(response.content[0])))
    out = llm.to_openai_messages(None, [{"role": "assistant", "content": [stored]}], "gemini")
    assert out[0]["tool_calls"][0]["extra_content"]["google"]["thought_signature"] == "sig"


def test_gemini_schema_is_cleaned_but_grok_keeps_strict():
    gemini = llm.to_openai_tools(TOOLS, "gemini")
    grok = llm.to_openai_tools(TOOLS, "xai")
    text = json.dumps(gemini)
    assert "additionalProperties" not in text and '"null"' not in text and '"nullable": true' in text
    assert all(t["function"].get("strict") for t in grok) and "additionalProperties" in json.dumps(grok)


def test_images_and_scanned_pdfs_per_provider():
    image = {"type": "image", "source": {"media_type": "image/png", "data": "QUJD"}}
    pdf = {"type": "document", "source": {"media_type": "application/pdf", "data": "QUJD"}}
    assert llm._part(image, "gemini")["image_url"]["url"].startswith("data:image/png;base64,")
    assert llm._part(pdf, "gemini")["image_url"]["url"].startswith("data:application/pdf;base64,")
    assert "cannot read" in llm._part(pdf, "xai")["text"]


def test_grok_gets_webp_converted_to_png():
    import base64
    import io
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (4, 4)).save(buffer, format="WEBP")
    block = {"type": "image", "source": {"media_type": "image/webp", "data": base64.b64encode(buffer.getvalue()).decode()}}
    assert llm._part(block, "xai")["image_url"]["url"].startswith("data:image/png;base64,")


@pytest.mark.parametrize("finish,expected", [("stop", "end_turn"), ("tool_calls", "tool_use"), ("length", "max_tokens"), ("content_filter", "refusal")])
def test_stop_reasons_map(finish, expected):
    assert llm.from_openai_response(api_reply("x", finish=finish)).stop_reason == expected


def test_blocked_reply_with_no_choices_is_a_refusal_not_a_crash():
    response = llm.from_openai_response(NS(choices=[], usage=None))
    assert response.stop_reason == "refusal" and response.content == []


def test_usage_separates_cached_tokens():
    usage = llm.from_openai_response(api_reply("x", prompt=100, completion=20, cached=30)).usage
    assert (usage.input_tokens, usage.output_tokens, usage.cache_read_input_tokens) == (70, 20, 30)


def test_malformed_tool_arguments_do_not_crash():
    bad = NS(id="c", function=NS(name="search_law", arguments="{not json"))
    response = llm.from_openai_response(api_reply(tool_calls=[bad], finish="tool_calls"))
    assert response.content[0].input == {}


def test_missing_tool_call_id_gets_one():
    call = tool_call("search_law", {"query": "q"}, tid="")
    assert llm.from_openai_response(api_reply(tool_calls=[call], finish="tool_calls")).content[0].id.startswith("call_")


def test_structured_output_request_for_audit():
    verdict = {"claims": [], "uncited_legal_claims": [], "invented_facts": [], "ignored_cautions": []}
    client, sdk = client_with([api_reply(json.dumps(verdict))])
    issues, _ = audit.audit_answer(client, "A cheque case is possible [[NIA1881:s138]].", "cheque bounced")
    assert issues == []
    fmt = sdk.requests[0]["response_format"]
    assert fmt["type"] == "json_schema" and "additionalProperties" not in json.dumps(fmt)


@pytest.mark.parametrize("provider", ["gemini", "xai"])
def test_the_gate_still_stops_invented_law_on_this_provider(provider):
    """The point of the whole design: the same checks hold whichever model is used."""
    verdict = json.dumps({"claims": [], "uncited_legal_claims": [], "invented_facts": [], "ignored_cautions": []})
    client, sdk = client_with([
        api_reply(tool_calls=[tool_call("get_provision", {"id": "TNRRA2017:s11"})], finish="tool_calls"),
        api_reply("Under Section 67B of the Rent Act your landlord must pay ten times the deposit."),
        api_reply("The deposit must be refunded within one month after you vacate [[TNRRA2017:s11]]."),
        api_reply(verdict),
    ], provider)
    session = agent.new_session()
    events = list(agent.run_turn(client, session, "My landlord in Chennai kept my deposit", today=TODAY))
    final = next(e for e in events if e["type"] == "final")
    assert "67B" not in final["text"] and final["verification"]["repairs"] == 1 and final["verification"]["status"] == "verified"
    assert any(m["role"] == "tool" for m in sdk.requests[1]["messages"])
    assert session["usage"]["api_calls"] >= 3


def test_provider_status_errors_reach_the_person_as_a_message(monkeypatch):
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    import httpx
    import openai

    class Failing:
        chat = NS(completions=NS(create=None))

    def boom(**_):
        raise openai.RateLimitError("slow down", response=httpx.Response(429, request=httpx.Request("POST", "http://x")), body=None)

    sdk = Failing()
    sdk.chat.completions.create = boom
    client = llm.OpenAICompatClient("gemini", sdk_client=sdk)
    events = list(agent.run_turn(client, agent.new_session(), "hello", today=TODAY))
    assert any(e["type"] == "error" for e in events) and not any(e["type"] == "final" for e in events)


def _rate_limit(message):
    import httpx
    import openai
    return openai.RateLimitError(message, response=httpx.Response(429, request=httpx.Request("POST", "http://x")), body=None)


def test_per_minute_rate_limit_waits_and_retries(monkeypatch):
    waits = []
    monkeypatch.setattr(llm.time, "sleep", waits.append)
    replies = [_rate_limit("Please retry in 7.5s."), api_reply("done")]

    class SDK:
        chat = NS(completions=NS(create=lambda **_: (_ for _ in ()).throw(r) if isinstance(r := replies.pop(0), Exception) else r))

    client = llm.OpenAICompatClient("gemini", sdk_client=SDK())
    out = client.messages.create(model="m", max_tokens=5, messages=[{"role": "user", "content": "x"}])
    assert out.content[0].text == "done" and waits == [8.5]


def test_daily_quota_fails_fast_instead_of_waiting(monkeypatch):
    import openai
    waits = []
    monkeypatch.setattr(llm.time, "sleep", waits.append)

    def boom(**_):
        raise _rate_limit("Quota exceeded for metric: generate_content_free_tier_requests, GenerateRequestsPerDay")

    client = llm.OpenAICompatClient("gemini", sdk_client=NS(chat=NS(completions=NS(create=boom))))
    with pytest.raises(openai.RateLimitError):
        client.messages.create(model="m", max_tokens=5, messages=[{"role": "user", "content": "x"}])
    assert waits == []


# ---- DeepSeek ----

def _message_with_reasoning(content, reasoning, tool_calls=None, finish="stop"):
    message = NS(content=content, tool_calls=tool_calls, reasoning_content=reasoning)
    return NS(choices=[NS(message=message, finish_reason=finish)], usage=None)


def test_deepseek_reasoning_is_sent_back_in_later_requests():
    """Its docs say a request with tools gets a 400 if earlier reasoning is missing."""
    call = tool_call("get_provision", {"id": "TNRRA2017:s11"})
    verdict = json.dumps({"claims": [], "uncited_legal_claims": [], "invented_facts": [], "ignored_cautions": []})
    client, sdk = client_with([
        _message_with_reasoning("", "I should read the law.", [call], "tool_calls"),
        _message_with_reasoning("The deposit comes back within one month [[TNRRA2017:s11]].", "Now I can answer."),
        api_reply(verdict),
    ], "deepseek")
    session = agent.new_session()
    final = [e for e in agent.run_turn(client, session, "My landlord kept my deposit", today=TODAY) if e["type"] == "final"][0]
    assert final["verification"]["status"] == "verified"
    assistant = [m for m in sdk.requests[1]["messages"] if m["role"] == "assistant"]
    assert assistant[0]["reasoning_content"] == "I should read the law."
    next_turn = llm.to_openai_messages(None, json.loads(json.dumps(session["messages"])), "deepseek")
    assert [m["reasoning_content"] for m in next_turn if m["role"] == "assistant"] == ["I should read the law.", "Now I can answer."]


def test_only_deepseek_gets_reasoning_sent_back():
    history = [{"role": "assistant", "content": [{"type": "thinking", "thinking": "x"}, {"type": "text", "text": "hi"}]}]
    assert "reasoning_content" not in llm.to_openai_messages(None, history, "gemini")[0]
    assert "reasoning_content" not in llm.to_openai_messages(None, history, "xai")[0]


def test_deepseek_audit_uses_json_mode_with_the_schema_in_the_prompt():
    verdict = {"claims": [], "uncited_legal_claims": [], "invented_facts": [], "ignored_cautions": []}
    client, sdk = client_with([api_reply(json.dumps(verdict))], "deepseek")
    issues, _ = audit.audit_answer(client, "A cheque case is possible [[NIA1881:s138]].", "cheque bounced")
    assert issues == []
    sent = sdk.requests[0]
    assert sent["response_format"] == {"type": "json_object"}
    assert "JSON" in sent["messages"][0]["content"] and "invented_facts" in sent["messages"][0]["content"]


def test_deepseek_blank_json_is_retried():
    verdict = {"claims": [], "uncited_legal_claims": [], "invented_facts": [], "ignored_cautions": []}
    client, sdk = client_with([api_reply("     "), api_reply(json.dumps(verdict))], "deepseek")
    issues, _ = audit.audit_answer(client, "Plain advice.", "facts")
    assert issues == [] and len(sdk.requests) == 2


def test_deepseek_malformed_audit_never_counts_as_a_pass():
    incomplete = json.dumps({"claims": []})
    client, sdk = client_with([
        api_reply("You can send a written demand and keep your receipts safe."),
        api_reply(incomplete), api_reply(incomplete),
    ], "deepseek")
    final = [e for e in agent.run_turn(client, agent.new_session(), "hello", today=TODAY) if e["type"] == "final"][0]
    assert final["verification"]["status"] == "citations_checked_only"


def test_deepseek_keeps_standard_tool_schemas_and_scanned_pdfs_get_a_clear_message():
    tools = llm.to_openai_tools(TOOLS, "deepseek")
    assert "strict" not in json.dumps(tools) and "additionalProperties" in json.dumps(tools)
    pdf = {"type": "document", "source": {"media_type": "application/pdf", "data": "QUJD"}}
    assert "cannot read" in llm._part(pdf, "deepseek")["text"]


def test_blank_text_is_not_treated_as_an_answer():
    response = llm.from_openai_response(api_reply("    "))
    assert response.content == []


# ---- Public deployment ----

def test_audit_falls_back_to_the_next_model_when_one_is_out_of_quota(monkeypatch):
    import httpx
    import openai
    from app import config
    monkeypatch.setattr(llm.time, "sleep", lambda _: None)
    monkeypatch.setattr(config, "AUDIT_MODEL", "strong")
    monkeypatch.setattr(config, "AUDIT_FALLBACKS", ["backup"])
    asked = []

    def create(**kwargs):
        asked.append(kwargs["model"])
        if kwargs["model"] == "strong":
            raise openai.RateLimitError("per day", response=httpx.Response(429, request=httpx.Request("POST", "http://x")), body=None)
        return api_reply(json.dumps({"claims": [], "uncited_legal_claims": [], "invented_facts": [], "ignored_cautions": []}))

    client = llm.OpenAICompatClient("gemini", sdk_client=NS(chat=NS(completions=NS(create=create))))
    issues, verdict = audit.audit_answer(client, "Plain advice.", "facts")
    assert asked == ["strong", "backup"] and verdict["_model"] == "backup"


def test_public_demo_limits_questions_per_visitor(monkeypatch):
    from fastapi.testclient import TestClient
    from app import config, server
    monkeypatch.setattr(config, "QUESTIONS_PER_HOUR", 2)
    monkeypatch.setattr(config, "api_ready", lambda: False)  # Stops before any model call.
    server._recent.clear()
    client = TestClient(server.app)
    codes = [client.post("/api/chat", json={"message": "hi"}, headers={"x-forwarded-for": "1.2.3.4"}).status_code for _ in range(3)]
    other = client.post("/api/chat", json={"message": "hi"}, headers={"x-forwarded-for": "5.6.7.8"}).status_code
    assert codes == [503, 503, 429] and other == 503
