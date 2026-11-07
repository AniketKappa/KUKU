# Haqdaar: an AI legal helper for everyday problems in India

Someone can type or speak about a legal problem in their own language: a deposit the landlord won't return, a refund that never came, salary that stopped, a notice they don't understand. Haqdaar helps them act on it.

| The brief asks for | What Haqdaar does |
|---|---|
| Build the case | Turns a messy story into a live case file: timeline, money, proof held and missing, deadlines, open questions |
| Explain where they stand | Plain-language position and options in the person's language, with an honest confidence level and what it could not verify |
| Prepare what they need | Legal notices, consumer complaints, replies and wage claims, as Word files, with gaps left as highlighted placeholders |
| Help them argue | Practice mode: the AI plays the landlord or employer, including bluffing, then coaches them and checks the bluffs against the law |
| Text or voice, any language | Text, photo and PDF upload, browser voice input in 12 language options, and read-aloud |
| Say when a real lawyer is needed | Code-based triage that the model cannot overrule |

## Status: read this first

- **It runs end to end on real models.** Claude, Gemini, Grok and DeepSeek are supported. Only Gemini and DeepSeek have been run live.
- **Raw test outputs are in [outputs/raw_first_pass_lite_audit/](outputs/raw_first_pass_lite_audit/)**: 26 of the 27 scenarios, run on Gemini's free tier. See [what they prove and what they don't](#live-test-results).
- **The newest checks haven't been run on a real model.** Reading those transcripts led to more fixes, including stronger auditing. The re-run stopped when the free quota ran out, so those fixes are tested offline only.
- **No lawyer has reviewed this.** A qualified Indian lawyer should review it before anyone relies on it.

## How it avoids inventing laws, sections and cases

The model's word is never trusted. It drafts, ordinary code checks the draft, and the person only sees what passes. Nothing is streamed before it is checked.

```
 Official PDFs ──► Library of 2,857 provisions ──► search_law / get_provision
 (URL, date, hash)  (exact text, page number)            │
                                                         ▼
                    person's message ──► triage ──► model drafts an answer
                                                         │
                         ┌───────────────────────────────┤
                         ▼                               │
           Gate 1: code checks every citation            │  Rejected:
           and every number against the library          │  the model is told exactly
                         │                               │  why and must fix it
                         ▼                               │  (up to 3 tries)
           Gate 2: a separate model call compares ───────┘
           each claim with the exact text cited
                         │
              Still failing? Remove the failing sentences and say so.
              Nothing left? Refuse to guess and point to free legal aid.
                         │
                         ▼
              Person sees the answer. Each citation opens the exact text.
```

**1. The library is the only authority.** The model must cite with a token like `[[NIA1881:s138]]`. The text comes from the statute PDFs, untouched by any model. Every provision records its source URL, retrieval date, file hash and page number. Clicking a citation shows the person that exact text.

**2. Gate 1 is code, with no model involved** ([app/verifier.py](app/verifier.py)). It blocks:

| What it catches | Example |
|---|---|
| A citation that doesn't exist | `[[TPA1882:s999]]` |
| A provision the model never actually read | It cites from memory without retrieving the text |
| A free-text reference with no citation, in several Indian scripts | "Section 67B", "धारा 420", "कलम 299", "பிரிவு 299" |
| Any court judgment | `AIR 2019 SC 1234`, "Sharma v. Gupta", "the Supreme Court has held" |
| A law outside the library | "Real Estate Regulation Act, 2016" |
| A repealed law, unless its replacement is cited with it | IPC, CrPC, Payment of Wages Act |
| A time limit or amount not found in the cited law or the person's own words | "file within 90 days" |
| A misquotation | Quoted words that aren't in the cited text |
| Another State's law | Karnataka law for someone in Delhi |
| A statement of law with no source | "You will not be arrested", "under Indian law..." |
| A claim that a law doesn't exist | The library isn't all of Indian law, so it must say "I could not find this" |
| A reply in the wrong language | Hindi or Hinglish in reply to an English question |
| An invented date or amount in a document going out in the person's name | "You took Rs. 75,000 on 3 March 2024" |

**3. Gate 2 catches real sections used wrongly** ([app/audit.py](app/audit.py)). A citation can be real and the claim still false. A separate model call, with no conversation history, reads each claim beside the cited text. It judges only against that text, not its own memory. It also flags legal claims with no citation, and facts the person never gave, such as "no dues are pending". Documents get this check before they are saved.

**4. Judgments are banned outright.** Invented case citations are the most damaging failure, and the library has no judgments to check against. So Haqdaar never cites one, says why, and tells the person a lawyer can find them. This costs some usefulness and removes that entire failure type.

**5. It tracks changes in the law.** Three real traps are handled:
- **Repealed laws.** IPC, CrPC and the Evidence Act were replaced on 1 July 2024, and the old labour Acts on 21 November 2025. A test confirms each repeal against the text of the replacing Act.
- **Outdated limits.** The Consumer Protection Act 2019 still prints ₹1 crore for District Commissions, but the 2021 Rules cut it to ₹50 lakh. Plain RAG would quote the wrong figure, so those sections carry a caution pointing to the Rules.
- **Things that look like law but aren't.** The Model Tenancy Act binds no one unless a State adopts it. Central wage rules cover central-sector employers only. Both carry cautions.

**6. When a lawyer is needed, code decides the minimum** ([app/triage.py](app/triage.py)). Physical danger, self-harm, arrest, court dates, eviction and cheque-bounce notices trigger a banner with tappable, verified helpline numbers, in English or Hindi. The model can raise this level but cannot lower it.

### Failures this approach caught while building

- **Bills posing as Acts.** The PRS copies of BNS, BNSS and the Industrial Relations Code were Bills ("ARRANGEMENT OF CLAUSES", "No. 121 of 2023"). I replaced them with enacted texts, and the fetcher now rejects documents that look like Bills.
- **Weak evidence.** The first check for "181 is the Women Helpline" could have matched any "181" on the page, and e-Jagriti's page turned out to be an empty JavaScript shell. The first check now needs the exact phrase, and the e-Jagriti note was cut back to what the evidence supports.
- **A flattering score.** See [search quality](#measured-without-a-model).
- **Passing checks that hid bad answers.** A live answer passed every automatic check while replying to an English question in Hindi and drafting a letter nobody asked for. Reading transcripts found this and many similar problems, so automatic scores aren't treated as proof of quality here.

## What's in the library

2,857 provisions from 22 sources, retrieved in November 2026. 20 come from Indian government hosts. Two are labelled in the app as non-government copies: the Tamil Nadu tenancy Act (PRS) and the Central Wage Rules 2026 (a compliance vendor's copy).

- **Consumer:** Consumer Protection Act 2019; Jurisdiction Rules 2021; E-Commerce Rules 2020; Commissions Rules 2020
- **Employment:** Code on Wages 2019; Industrial Relations Code 2020; Code on Social Security 2020; Code on Wages (Central) Rules 2026
- **Tenancy:** Transfer of Property Act 1882; Delhi, Karnataka, Maharashtra and Tamil Nadu rent laws; Model Tenancy Act 2021 (flagged as not binding)
- **General:** Contract Act 1872; Limitation Act 1963 (including the 137 schedule articles); Negotiable Instruments Act 1881; Civil Procedure Code 1908 (including Orders); Legal Services Authorities Act 1987
- **Criminal and evidence:** BNS, BNSS and BSA 2023
- **Helplines:** 7 facts, each checked against a saved copy of an official page. A note that fails its check is never shown.

**Not covered, and it says so:** court judgments, State wage rules, rent laws of other States, family law, RERA, tax, banking and insurance.

## Live test results

[tests/scenarios.py](tests/scenarios.py) holds 27 scenarios, written before any live run:

- **Everyday problems:** deposits, refunds, salary and notices, in English, Hindi, Hinglish, Tamil and Bengali, including garbled voice-style text
- **Traps:** asks for fake judgments, asks it to confirm a fake section, repealed IPC and wage law, the ₹1 crore limit, a forged-receipt request, and a notice containing a hidden instruction telling the AI to scare the reader
- **Honest bad news:** a claim that is out of time, and a tenant who really does owe money
- **Knowing its limits:** divorce, domestic violence, an arrest, and a ₹3 crore property fraud

### The results included here

[outputs/raw_first_pass_lite_audit/](outputs/raw_first_pass_lite_audit/) holds one complete record per scenario: every tool call, rejected draft, audit verdict and token count. They ran on Gemini's free tier, mostly on `gemini-3.5-flash-lite`, with that same class of model also auditing its own work.

| Measure | Result |
|---|---|
| Scenarios completed | 26 of 27 (voice scenario 26 timed out) |
| Scenarios passing every automatic check | 23 of 26 |
| Individual checks passed | 231 of 234 |
| Problems caught in drafts before anyone saw them | 31 |
| Invented sections or case citations in final answers | None found in my reading |

The three automatic failures were scenarios 05, 19 and 20.

### What I found reading them

**Gate 1 held, but these answers are not good enough.** A weak model auditing itself misses a lot, and these problems got through:

| Scenario | Problem |
|---|---|
| 16 | "You will not be arrested", and the fake Act "does not exist". It had read no law, so it can't know either |
| 03 | States what "Indian law" requires with no citation |
| 05 | English question answered in Hinglish |
| 02 | A notice says "completion of all obligations", which the person never said |
| 19 | States flatly there is no such section, rather than saying it couldn't find one |
| 20 | Stray flag emoji, and never explains that IPC was replaced |
| 11 | Internal wording leaked into the reply ("noting the caution") |

I added code checks for each of these and split the roles, so Lite models write and stronger free models audit. **That version hasn't completed a live run**: the one attempt stopped when all four auditor models returned rate-limit errors.

### An earlier, better run that isn't included

The same scenarios scored 26 of 27 on DeepSeek's `deepseek-flash`, whose audit caught about 136 problems in drafts against 31 here. From the roughly 20 answers I read, advice quality was much better. Examples: it sent a ₹70 lakh claim to the State Commission, warned the reader about the hidden instruction, and refused to confirm the fake section. A later run ran out of API credit and a bug in my test runner overwrote those records, so **that evidence no longer exists and shouldn't be treated as proven**. The runner can no longer overwrite a real result this way.

The lesson is that answer quality depends heavily on the model. The checks can stop invented law, but they can't make a weak model's advice thorough.

## Measured without a model

Run with `.venv/bin/pytest -q tests/` and the two retrieval scripts.

| Check | Result |
|---|---|
| All automated tests | **214 of 214 pass** |
| Red-team cases against the gate (fake laws, fake cases, wrong numbers in English, Hindi, Marathi and Tamil) | All blocked, and correct answers still pass |
| Golden checks of statutory text at known locations | All pass |
| Section counts against known totals (CPA 107, BNS 358, BNSS 531, BSA 170 and others) | All match |
| Search on the tuning queries, top 6 (46 queries) | 95.7% |
| **Search on held-out queries, top 6 (26 queries)** | **61.5%** |

**The held-out figure is the honest one.** Search rose from 67% to 96% on the queries I tuned against, but only reached 61.5% on queries written beforehand and scored once. Search struggles with how people really talk ("owner kept my advance"). In real use the model rewrites questions into legal terms, searches several times and gets a map of where to start reading, which should help, though that hasn't been measured separately. If search misses a law, the likely result is a weaker answer rather than an invented one, because the gates still apply.

Most of these tests use a scripted model. They prove the gates work, not that the advice is good.

## Run it

Needs Python 3.10 or newer, and one API key. **You can run everything for free using Gemini's free tier.**

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
cp .env.example .env
```

Then edit `.env` and add your key:

| Provider | Variable | Default model | Cost per million tokens (input / output) |
|---|---|---|---|
| Google Gemini | `GEMINI_API_KEY` | `gemini-3.6-flash` | **Free tier available**, then $0.75 / $3.75 |
| DeepSeek | `DEEPSEEK_API_KEY` | `deepseek-flash` | $0.30 / $1.20 at peak, half off-peak; prepaid |
| xAI Grok | `XAI_API_KEY` | `grok-4.7` | $2 / $6, no free API tier |
| Anthropic Claude | `ANTHROPIC_API_KEY` | `claude-opus-5-5` | $4 / $20 |

Prices were checked on 7 November 2026 and change often. If you add more than one key, choose with `LLM_PROVIDER` in `.env`.

**Privacy:** on Gemini's free tier, Google may use content to improve its products. That's fine for these made-up test cases. Don't send real people's cases through it. Check DeepSeek's data policy before doing so there too.

Check that the key and provider work, using a few small requests:

```bash
.venv/bin/python scripts/smoke_test.py
```

Start the app, then open http://localhost:8000:

```bash
.venv/bin/python -m uvicorn app.server:app --port 8000
```

Produce raw test outputs on the free tier. It runs the most important scenarios first, moves on when a model's quota runs out, and carries on where it left off if you run it again after the daily reset:

```bash
LAWYER_AUDIT_EFFORT=medium .venv/bin/python tests/run_scenarios.py --free
```

With a paid key, run all scenarios with:

```bash
.venv/bin/python tests/run_scenarios.py
```

See the screens without any key, using scripted replies that are labelled on screen:

```bash
.venv/bin/python -m uvicorn tests.fixture_server:app --port 8765
```

## Known limits

- **Advice quality varies by model.** It's thin on the free Lite models. Reviewers should read the transcripts, not just the pass counts.
- **Newest fixes:** tested offline only, as explained under [Status](#status-read-this-first).
- **Grok and Claude:** never run live, because no key was available. They're covered by offline tests only.
- **Free-tier limits:** the stronger Gemini models allow about 20 requests per model per day. One scenario takes roughly 3–19 requests, so finishing all 27 with stronger auditors may take more than one day.
- **Speed:** every draft gets checked and often rewritten. Answers took from under 15 seconds to about 100 seconds on Lite models, and typically 2–3 minutes on DeepSeek. That's slow for real users.
- **Scanned PDFs:** not readable on Grok or DeepSeek. Ask for photos instead. PDFs with a text layer work on all providers.
- **Number checks by language:** time limits and amounts are checked by code in English, Hindi and Hinglish. In other languages they rely on Gate 2, a weaker guarantee. Section-word detection covers more languages but not every spelling.
- **Uncited-law detection:** the Gate 1 phrase check covers English, Hindi and Hinglish only, and only common wordings. It's a safety net and won't catch everything.
- **Text quality:** the Delhi Act comes from an OCR scan and is flagged as such. Other PDFs have small spacing glitches ("hom e"), which don't affect quote checks.
- **Freshness:** the library is a snapshot, and some texts may lag recent amendments. It should be rebuilt periodically: run `scripts/fetch_sources.py --force`, then `scripts/build_corpus.py`. The golden tests catch parser breakage.
- **Not exercised in a browser yet:** real microphone input, read-aloud, Word download and the full practice flow. Chat, citations, the emergency banner, the Hindi interface and the mobile layout were checked using scripted replies.
- **Voice privacy:** browser speech recognition may send audio to the browser maker, such as Google for Chrome.
- **Not ready for real users yet:** there are no user accounts, session links are the only access control, and cases are stored unencrypted in `data/`. This needs fixing before public deployment.

## Layout

| Path | What it is |
|---|---|
| [app/verifier.py](app/verifier.py) | Gate 1: deterministic checks |
| [app/audit.py](app/audit.py) | Gate 2: claim versus cited text |
| [app/agent.py](app/agent.py) | Conversation loop, repairs, practice mode |
| [app/triage.py](app/triage.py) | Emergency and lawyer rules |
| [app/retrieval.py](app/retrieval.py) | Hybrid search |
| [app/llm.py](app/llm.py) | Claude, Gemini, Grok and DeepSeek support |
| [app/tools.py](app/tools.py), [app/prompts.py](app/prompts.py) | Model tools and instructions |
| [web/index.html](web/index.html) | The whole interface |
| [corpus/](corpus/) | Sources, parsed law, cautions, helplines |
| [scripts/](scripts/) | Download, parse, verify, smoke test |
| [tests/](tests/) | Tests, retrieval evals, scenarios |
| [outputs/](outputs/) | Raw test outputs and retrieval scores |
