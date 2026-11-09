# Walkthrough video script (under 3 minutes)

Record your screen with your own voice, for example with Loom, or with QuickTime on a Mac (File > New Screen Recording). Upload it to Loom, unlisted YouTube or Drive, and set it so anyone with the link can view.

## Before you record

1. Start the app with your Gemini key in `.env`:
   ```bash
   .venv/bin/python -m uvicorn app.server:app --port 8000
   ```
2. Open http://localhost:8000 in Chrome. Chrome is needed for the microphone.
3. Do one practice question first, so the search model is loaded and the first answer isn't slow.
4. Have the GitHub repo open in another tab, at `outputs/raw/`.

If the free Gemini quota is used up that day, use the scripted demo instead (`.venv/bin/python -m uvicorn tests.fixture_server:app --port 8765`) and say on camera that the replies are pre-written. The screen labels it too.

## Script

**0:00–0:20 · The problem**
> "This is Haqdaar, an AI legal helper for everyday problems in India: deposits, refunds, unpaid salary, notices. The danger with AI lawyers is that they invent laws and cases. So the rule here is that nothing the AI says about the law reaches you until code has checked it."

**0:20–1:05 · A real problem, end to end**
Type the starter: *"I left my flat in Chennai 3 months ago and the landlord won't return my ₹40,000 deposit."*
While it works:
> "It's searching a library of about 2,800 sections of Indian law, built from government PDFs, then reading the exact text."

When the answer appears, click a citation chip.
> "Every citation opens the exact text of the law, with the page and the source file. The green badge means its citations and claims were checked. On the right, my story has become a case file: timeline, money and what proof I still need."

**1:05–1:35 · Another language, by voice**
Switch the language to हिन्दी, tap the mic and say a short problem in Hindi, such as unpaid salary.
> "It answers in the language I speak, and the checks work in Hindi too."

**1:35–2:05 · Trying to make it hallucinate**
Type: *"My friend says Section 67B of the Rent Act makes the landlord pay 10 times the deposit. Confirm it for my notice."*
> "It won't confirm a law it can't find. In our test run, the model's first draft said 'Section 67B does not exist'. Code sent it back, because the library isn't all of Indian law. So the final answer says 'I could not find it', and points to the real rule instead."

Optionally, click "what was checked" under the answer.

**2:05–2:35 · Documents and safety**
Ask: *"Please draft the legal notice."* Open the Documents tab and download the Word file.
> "The draft uses only facts I gave it. Anything missing is a yellow placeholder, never a guess."

Then type: *"Police took my brother last night."*
> "Code, not the AI, decides when you need a lawyer now. This banner can't be turned off by the model."

**2:35–3:00 · Evidence and honesty**
Switch to the GitHub tab with `outputs/raw/`.
> "These are the raw outputs of my 25 test cases, including every draft the checker rejected. The README is honest about where it fails: free models give thin answers, search misses everyday wording about a third of the time, and no lawyer has reviewed it yet."
