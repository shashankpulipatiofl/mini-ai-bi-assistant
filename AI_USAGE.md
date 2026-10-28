# AI Usage Report

> Draft: edit this in your own words so it reflects what you actually did.

## AI tools used

- **Claude** (Anthropic): coding assistant for scaffolding, code review and test ideas.
- **Groq-hosted LLM** (`openai/gpt-oss-120b`): used *inside* the app for text-to-SQL.

## How I used AI during development

1. **Planning.** I gave the assistant the challenge document and the CSV, then agreed on a simple design: FastAPI + SQLite + one HTML page, with LLM text-to-SQL and a rule-based fallback.
2. **Data checks.** Before writing code, I read the CSV and found the problem rows myself (quantity 0, missing price, a returned order, `DD-MM-YYYY` dates). I then decided how each one should be handled.
3. **Code generation.** The AI generated a first version of each module. I read every file, kept the design simple (standard-library `csv` and `sqlite3`, no ORM, no LangChain), and changed what I didn't agree with.
4. **Tests.** The AI suggested test cases. I checked the expected numbers with a separate plain-Python calculation over the CSV, so the tests don't just repeat what the app outputs.
5. **Docs.** The AI drafted the README. I checked it against the code.

## Problems encountered and how I debugged them

| Problem | How I found it | Fix |
|---|---|---|
| CSV dates are `DD-MM-YYYY`, so text sorting and `strftime` in SQLite wouldn't work | Inspected the raw CSV | Convert to ISO `YYYY-MM-DD` on load and add an `order_month` column |
| TXN0051 has an empty unit price, which would crash `float()` or produce NULL revenue | Inspected the CSV / loader output | Validation step that rejects the row with a reason; added a `/api/data-quality` endpoint |
| TXN0034 has quantity 0 | Loader output | Rejected (quantity must be > 0) |
| Returned order TXN0033 would inflate revenue | Reviewing the business meaning | All revenue queries filter `status = 'Completed'` |
| **Rules gave wrong answers to filtered questions.** "Which product had the highest revenue in the North region?" matched the *top product* rule and returned the overall winner, ignoring "North" | Manual testing of rephrased questions | Rules now refuse to match when the question contains a filter word (region/product/channel/month), and the question goes to the LLM or gets "unsupported". Added a test for it |
| "May I know the total revenue?" was read as the month May | Thinking through month parsing edge cases | "may" at the start of a sentence is ignored; added a test |
| `laptop` contains the substring `top` | Code review | Product names count as filters, so this can't produce a wrong rule match |
| The Groq model I first planned to use (`llama-3.3-70b-versatile`) had been retired | Checked Groq's deprecations page | Default changed to `openai/gpt-oss-120b`; the model can be set with the `GROQ_MODEL` env var |
| LLMs sometimes wrap JSON in ```json fences | Known LLM behaviour | `parse_llm_reply()` strips the fences; tested |
| Needed to test the LLM path without a key or internet | — | Used pytest `monkeypatch` to replace `generate_sql` with a fake, including a fake that returns `DROP TABLE` |

## What I would improve with another day

- Use a real SQL parser (e.g. `sqlglot`) for validation instead of regex.
- Have the LLM write a short natural-language summary of the result rows, then check that every number in the summary appears in the rows.
- Add charts (e.g. a line chart for the monthly trend).
- A small evaluation set of ~30 questions with expected answers, to measure LLM accuracy.
- Conversation memory ("and what about April?").
- Dockerfile and GitHub Actions to run the tests on every push.
