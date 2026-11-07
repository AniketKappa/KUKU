"""Cheap check that your API key and provider work, before spending on the full test run.

Run: .venv/bin/python scripts/smoke_test.py

It tries each feature the app needs from a provider, using a handful of small requests.
Nothing here tests legal quality. That is what tests/run_scenarios.py is for.
"""
import base64
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import agent, audit, config, llm  # noqa: E402
from app.tools import TOOLS  # noqa: E402


def check(name, fn):
    try:
        detail = fn()
        print(f"PASS  {name}: {detail}")
        return True
    except Exception as exc:
        print(f"FAIL  {name}: {type(exc).__name__}: {str(exc)[:400]}")
        return False


def main():
    print(f"Provider: {config.PROVIDER}   Model: {config.MODEL}   Key found: {config.api_ready()}")
    if not config.api_ready():
        sys.exit("No key found. Add one to .env: DEEPSEEK_API_KEY, GEMINI_API_KEY, XAI_API_KEY or ANTHROPIC_API_KEY.")
    client = llm.make_client()
    session = agent.new_session()

    def plain():
        r = agent.call_model(client, session, "Reply with one word.", [{"role": "user", "content": [{"type": "text", "text": "Say ok."}]}], effort="low")
        return agent.text_of(r)[:60]

    def tool_round_trip():
        history = [{"role": "user", "content": [{"type": "text", "text": "Use get_provision to read NIA1881:s138. Call the tool now."}]}]
        r = agent.call_model(client, session, "You must call the requested tool.", history, tools=TOOLS, effort="low")
        uses = [b for b in r.content if b.type == "tool_use"]
        if not uses:
            raise RuntimeError(f"no tool call; stop={r.stop_reason}")
        history.append({"role": "assistant", "content": [agent.block_to_dict(b) for b in r.content]})
        history.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": u.id, "content": "It covers dishonoured cheques."} for u in uses]})
        # Sending the tool result back is where hidden-signature rules usually fail.
        second = agent.call_model(client, session, "Answer in one short sentence.", history, tools=TOOLS, effort="low")
        return f"called {uses[0].name}({json.dumps(uses[0].input)}), then replied: {agent.text_of(second)[:80]}"

    def all_tool_schemas():
        history = [{"role": "user", "content": [{"type": "text", "text": "Save a case file: tenant in Chennai, deposit unpaid. Then call set_assessment."}]}]
        r = agent.call_model(client, session, "Use the tools.", history, tools=TOOLS, effort="low")
        return f"accepted {len(TOOLS)} tool schemas; called {[b.name for b in r.content if b.type == 'tool_use']}"

    def structured():
        issues, verdict = audit.audit_answer(client, "A cheque case is possible [[NIA1881:s138]].", "my cheque bounced")
        return f"{len(verdict['claims'])} claim(s), {len(issues)} issue(s)"

    def vision():
        data = base64.b64encode((ROOT / "tests" / "fixtures" / "cheque_notice.png").read_bytes()).decode()
        text = agent._transcribe_upload(client, session, "image/png", data)
        if "85,000" not in text:
            raise RuntimeError(f"did not read the amount: {text[:200]}")
        return f"read {len(text)} characters, including the amount"

    results = [check("Plain reply", plain), check("Tool call and result", tool_round_trip), check("All tool schemas", all_tool_schemas),
               check("Structured audit", structured), check("Reading a photo", vision)]
    print(f"\n{sum(results)} of {len(results)} passed. Tokens used: {session['usage']}")
    sys.exit(0 if all(results) else 1)


if __name__ == "__main__":
    main()
