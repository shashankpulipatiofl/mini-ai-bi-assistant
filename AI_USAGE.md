# AI Usage Report

> Draft: edit this in your own words so it reflects what you actually did.

## AI tools used

- **Claude** (Anthropic): coding assistant for scaffolding, code review and test ideas.
- **Groq-hosted LLM** (`openai/gpt-oss-120b`): used *inside* the app to turn questions into SQL and to write the final answer.

## How I used AI during development

1. **Planning.** I gave the assistant the challenge document and the CSV, then chose a simple design: FastAPI + SQLite + one HTML page, with an LLM for text-to-SQL.
2. **Data checks.** I inspected the CSV for problem rows (quantity 0, missing price, a returned order, `DD-MM-YYYY` dates) and decided how each should be handled.
3. **Code generation.** The AI generated a first version of each module. I read every file and kept it simple: standard-library `csv` and `sqlite3`, no ORM, and no framework like LangChain, so every step is visible.
4. **Design change.** My first version answered the 5 example questions with keyword rules and used the LLM only for other questions. I removed the rules: the task asks for an AI interpretation mechanism, and keyword matching broke on rephrased questions. The app now uses the LLM for every question, with validation guards around it.
5. **Tests.** The AI suggested test cases. I checked the expected numbers with a separate plain-Python calculation over the CSV, and replaced the LLM with a fake in tests so they are repeatable.
6. **Docs.** The AI drafted the README. I checked it against the code.

## Problems encountered and how I debugged them

| Problem | How I found it | Fix |
|---|---|---|
| CSV dates are `DD-MM-YYYY`, which doesn't sort or group correctly as text | Inspected the raw CSV | Convert to ISO `YYYY-MM-DD` on load and add an `order_month` column |
| TXN0051 has an empty unit price, which would crash `float()` or give NULL revenue | Loader output | Validation rejects it with a reason; added `/api/data-quality` |
| TXN0034 has quantity 0 | Loader output | Rejected (quantity must be > 0) |
| Returned order TXN0033 would inflate revenue | Thinking about the business meaning | Revenue rule `status = 'Completed'` added to the LLM prompt |
| The LLM might re-derive revenue wrongly (e.g. forget the discount) | Prompt review | Pre-computed a `revenue` column at load time, so the LLM only has to `SUM(revenue)` |
| The keyword-rule version answered "top product **in the North region**" with the overall top product | Manual testing | Removed the rules and switched to LLM-only text-to-SQL, which handles filters |
| The Groq model I first planned to use (`llama-3.3-70b-versatile`) had been retired | Checked Groq's deprecations page | Default changed to `openai/gpt-oss-120b`; configurable with `GROQ_MODEL` |
| Tests can't depend on a paid, non-deterministic API | — | pytest `monkeypatch` swaps in a fake LLM, including one that returns `DROP TABLE` and one that makes a SQL typo |
| The LLM can return broken SQL | — | The SQLite error is sent back to the LLM for one retry (tested with a deliberate typo) |
| LLMs sometimes wrap JSON in ```json fences | Known LLM behaviour | `parse_json_reply()` strips the fences; tested |
| My cloud dev environment couldn't reach `api.groq.com` (blocked by a proxy) | Got 403 errors; `curl` showed the proxy refused the connection | Tested the pipeline with the fake LLM; real calls are tested on my own machine |

## What I would improve with another day

- Use a real SQL parser (e.g. `sqlglot`) instead of regex for validation.
- Automatically check that every number in the answer text appears in (or is calculated from) the result rows.
- An evaluation set of ~30 questions with expected SQL results, to measure real LLM accuracy.
- Turn it into a tool-calling agent: the LLM can run several queries for multi-step questions.
- Charts for trends; conversation memory for follow-up questions.
- Dockerfile and GitHub Actions to run the tests on every push.
