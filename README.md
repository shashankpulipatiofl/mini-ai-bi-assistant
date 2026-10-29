# Mini AI Business Intelligence Assistant

Ask questions about sales data in plain English. An LLM first decides what kind of question it is:

- **Data questions** ("Which region had the highest sales?"): the LLM writes SQL, the SQL is validated and run on SQLite, and the answer comes with the supporting figures.
- **Questions about the dataset** ("What columns are there?"): answered with **vectorless RAG** over a data dictionary document (`docs/data_dictionary.md`), using only the retrieved sections.
- **Anything else** is politely refused.

Built for the GET AI-Assisted Development Challenge (target effort 3–4 hours). It is a small working app, not a production system. See [REQUIREMENTS_CHECKLIST.md](REQUIREMENTS_CHECKLIST.md) for where each requirement is met, and [AI_USAGE.md](AI_USAGE.md) for how I used AI.

**Stack:** Python · FastAPI (API) · SQLite (database) · Groq LLM `openai/gpt-oss-120b` (text-to-SQL) · plain HTML/JS (UI) · pytest (tests)

---

## 1. Setup and run

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env              # then put your key in .env:  GROQ_API_KEY=gsk_...
                                  # free key: https://console.groq.com/keys

python -m uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000** for the UI, or **http://127.0.0.1:8000/docs** for the Swagger API docs.

```bash
python -m pytest -v               # run the automated tests (no API key needed)
python -m scripts.check_examples  # ask the REAL LLM the 5 example questions and check the numbers
python -m app.database            # load the CSV only; prints loaded/rejected rows
```

### Example

```bash
curl -X POST http://127.0.0.1:8000/api/ask \
     -H "Content-Type: application/json" \
     -d '{"question": "Which region generated the highest sales?"}'
```

```json
{
  "question": "Which region generated the highest sales?",
  "question_type": "data",
  "answer": "The South region generated the highest sales with 411,200.00, followed by North (384,450.00).",
  "sql": "SELECT region, ROUND(SUM(revenue), 2) AS revenue FROM sales WHERE status = 'Completed' GROUP BY region ORDER BY revenue DESC",
  "explanation": "Total completed revenue per region, highest first.",
  "columns": ["region", "revenue"],
  "rows": [["South", 411200.0], ["North", 384450.0], ["East", 346700.0], ["West", 296850.0]],
  "row_count": 4,
  "attempts": 1
}
```

(The `answer` and `explanation` wording comes from the LLM, so it can vary a little. The numbers come from the database.)

For a dataset question such as `"What columns are in the dataset?"`, the response has `"question_type": "dataset_info"` and `"sql": null`, and `columns`/`rows` contain the documentation sections that were retrieved.

| Endpoint | Purpose |
|---|---|
| `GET /` | Web UI |
| `POST /api/ask` | Body `{"question": "..."}` → structured answer |
| `GET /api/health` | Status, and whether an LLM key is configured |
| `GET /api/data-quality` | Rows rejected while loading the CSV, with the reason |

| HTTP code | When |
|---|---|
| 200 | Answer returned |
| 400 | Empty question, or longer than 300 characters |
| 422 | Request body is malformed, the question can't be answered from the data or the docs, or the generated SQL was blocked or failed twice |
| 404 | The query was valid but found no matching data (e.g. a month that isn't in the dataset) |
| 503 | The LLM is unavailable (no API key, wrong key, rate limit, network error, bad reply) |

---

## 2. Architecture

```
 Browser (static/index.html)
        │  POST /api/ask {"question": "..."}
        ▼
 FastAPI (app/main.py) ── turns errors into HTTP status codes
        │
        ▼
 qa_service.answer_question()  (app/qa_service.py)
   1. check input ──────────────────────────────────────────────▶ 400
   2. ROUTER  llm.route_question() → data | dataset_info | unsupported
        │
        ├─ unsupported ─────────────────────────────────────────▶ 422
        │
        ├─ dataset_info ── RAG (app/rag.py) ─────────────────────────────┐
        │     retrieve top 3 sections of docs/data_dictionary.md         │
        │     (keyword matching, no vectors) → LLM answers ONLY from     │
        │     them. Nothing found / not in docs ────────────────▶ 422    │
        │                                                                │
        └─ data ── text-to-SQL ──────────────────────────────────────────┤
              a. llm.generate_sql() → {"sql", "explanation"}             │
                 sql = null ────────────────────────────────────▶ 422    │
              b. sql_validator.validate_sql()  SELECT only, sales only   │
                 unsafe ────────────────────────────────────────▶ 422    │
              c. database.run_query()  read-only SQLite                  │
                 SQL error → send error back to LLM, retry once          │
              d. no rows ───────────────────────────────────────▶ 404    │
              e. llm.write_answer()  rows → short answer                 │
                                                                         ▼
   3. structured JSON: {question_type, answer, sql, explanation, columns, rows, ...}
```

| File | Responsibility |
|---|---|
| `app/database.py` | Load and clean the CSV into SQLite; run read-only queries |
| `app/llm.py` | Groq API calls: route the question, question → SQL, rows → answer text |
| `app/rag.py` | Vectorless RAG: split the docs into sections, keyword retrieval, grounded answer |
| `app/sql_validator.py` | Checks that a generated SQL query is safe before it runs |
| `app/qa_service.py` | The pipeline above |
| `app/main.py` | FastAPI endpoints and error handling |
| `docs/data_dictionary.md` | Knowledge document for questions about the dataset |
| `static/index.html` | One-page UI |
| `tests/test_app.py` | Automated tests (the LLM is replaced by a fake) |
| `scripts/check_examples.py` | Manual check of the real LLM on the 5 example questions |

---

## 3. Data management

**How the data is stored.** On startup the CSV is read row by row (Python `csv` module), validated, and inserted into SQLite (`data/sales.db`). The database is rebuilt from the CSV on every start, so the CSV stays the single source of truth.

- `sales` holds the valid rows, with proper types. Dates are converted from `DD-MM-YYYY` to ISO `YYYY-MM-DD`. Two derived columns are added:
  - `order_month` (`YYYY-MM`), which makes monthly questions easy for the LLM.
  - `revenue = quantity × unit_price − discount_amount`, computed once, so the LLM never has to re-derive the formula.
- `rejected_records` holds invalid rows plus the reason, kept for auditing (visible at `/api/data-quality`).

**Business rule:** revenue counts only `status = 'Completed'` orders. The returned order stays in `sales` but is filtered out. This rule is written into the LLM prompt.

**Validation per row:** required fields present · valid `DD-MM-YYYY` date · quantity is an integer > 0 · unit price > 0 · 0 ≤ discount ≤ order value · status is `Completed` or `Returned` · no duplicate IDs.

| Row | Problem | Handling |
|---|---|---|
| TXN0034 | Quantity = 0 | Rejected |
| TXN0051 | Unit price missing | Rejected (I didn't guess a price) |
| TXN0033 | Status = Returned | Kept, but excluded from revenue |

Result: 49 rows loaded, 48 Completed. Total revenue = **1,439,200.00**. I checked this against a separate plain-Python calculation over the CSV.

**How it is queried.** Every answer comes from a `SELECT` query on the `sales` table. The query is written by the LLM and returned in the response, so the user can see how each number was calculated.

---

## 4. AI integration and how wrong answers are prevented

The LLM (Groq, `openai/gpt-oss-120b`, `temperature=0`, JSON mode) is used in small, single-purpose steps:

1. **Router.** It classifies the question as `data`, `dataset_info` or `unsupported`. Each type gets the right tool, instead of forcing everything through SQL.
2. **Text-to-SQL** (data questions). The prompt contains the table schema, sample values, the revenue rule (`status = 'Completed'`), output rules, and an instruction to return `"sql": null` if the question can't be answered.
3. **Answer writing** (data questions). The LLM gets the question and the query result rows, and writes 1–3 sentences using *only* those numbers.
4. **RAG over the data dictionary** (dataset questions). This is how you answer from documents. **Retrieve:** `docs/data_dictionary.md` is split at its `##` headings into sections. Each section is scored by how many keywords from the question it contains (with simple synonyms, e.g. "attributes" → "column"), and the top 3 are kept. **Augment:** only those sections go into the prompt. **Generate:** the LLM must answer only from them, or return `null`.
   - It is **vectorless**: there are no embeddings and no vector database. For a small, well-structured document, keyword retrieval is enough, needs no extra libraries, and is easy to debug. For a large document set I would switch to embeddings or BM25.
   - The retrieved sections are returned and shown in the UI, so the user can check the source.

**Guards:**

| Risk | Guard |
|---|---|
| Destructive SQL (`DROP`, `DELETE`…) | `sql_validator.py`: single statement, must start with `SELECT`, no write/admin keywords, no comments, only the `sales` table. Unsafe SQL is **never** executed. |
| Validator misses something | The SQLite connection is opened **read-only** (`mode=ro`), so the database itself refuses writes |
| Question not about this data | The router returns `unsupported` → 422. The SQL prompt can also return `sql: null` as a second check |
| Dataset question not covered by the docs | No section retrieved, or the LLM returns `answer: null` → 422 instead of a made-up answer |
| LLM writes broken SQL | The SQLite error is sent back to the LLM for **one** retry; if it still fails → 422 |
| LLM invents numbers | The numbers come from SQL results. The answer prompt says to use only the given rows, and the rows are always shown next to the answer. |
| Wrong logic hidden from the user | The SQL and an explanation are returned with every answer, so they can be checked |
| Answer-writing call fails | The numbers are still correct, so a plain "N rows returned" answer is used instead of an error |
| Huge results | Results are capped at 200 rows; questions at 300 characters |

---

## 5. Testing

`python -m pytest -v` runs **34 tests**. The LLM is replaced by a fake with pytest `monkeypatch`, so the tests need no key or internet and always give the same result. The fake returns what a correct LLM would return. The tests check that the app routes, validates, runs and answers correctly using the real database and the real docs.

| Type | Tests |
|---|---|
| Valid data questions (all 5 examples) | total revenue = 1,439,200 · top product = Monitor · 6-month trend · top region = South · March vs April rows |
| Dataset questions (RAG) | retrieval picks "Columns and attributes" for an attributes question and "Order status" for a status question · answer comes back with the retrieved sections and no SQL · the docs really are put into the prompt · a question with nothing relevant in the docs → 422 · the LLM saying "not in docs" → 422 |
| Invalid questions | empty (400) · missing field (422) · too long (400) · router says unsupported (422) · SQL step says not answerable (422) · no API key (503) · wrong API key gives a clear message (503) · router returns an unknown type (503) |
| SQL safety | the LLM returning `DROP TABLE` is blocked and the data stays intact · 8 unsafe queries rejected by the validator · a normal SELECT allowed |
| Edge cases | bad CSV rows rejected · returned orders excluded · month with no data (404) · the LLM fixes its own SQL error on retry · gives up after 2 bad queries · answer-writing failure still returns the numbers · JSON in ``` fences parsed |

---

## 6. Known limitations

- **Needs an API key and internet.** Without a key, `/api/ask` returns 503.
- **Up to 3 LLM calls per data question** (router, SQL, answer). That's slower and costs more than one call, but each step is simpler and easier to check.
- **The router can misclassify a question.** It's an LLM decision. A wrong type leads to a refusal or a less useful answer, not wrong numbers.
- **RAG retrieval is keyword-based.** A question phrased with words that aren't in the docs (and aren't in the small synonym list) may retrieve nothing, which gives a 422.
- **The data dictionary is written by hand.** If the CSV changes, the document must be updated too.
- **The LLM can write SQL that is safe but logically wrong** (e.g. it forgets the `Completed` filter). The validator checks safety, not business meaning. The SQL is shown so a user can check it.
- **The answer text comes from an LLM.** It is told to use only the result rows, but this isn't checked automatically. The supporting table is the source of truth.
- **Answer wording can vary slightly between runs**, even at temperature 0.
- **The validator uses regex, not a real SQL parser.** It also blocks the harmless SQLite `REPLACE()` function.
- **The tests use a fake LLM**, so they test the pipeline and its guards, not the quality of the real model's SQL. `scripts/check_examples.py` covers the real model, but only for the 5 example questions.
- **Rejected rows are excluded.** TXN0051's price could be filled in (Laptop Stand is always 1,200), but I chose not to guess financial data.
- **Months without a year are assumed to be 2026** (the only year in the data).
- No authentication, no conversation memory, no charts. The database is rebuilt on every start.

## 7. What works / what doesn't

- **Works:** all 5 example questions, free-form data questions (e.g. "revenue by sales channel", "top product in the North region"), questions about the dataset via RAG (e.g. "what columns are there?", "how is revenue calculated?"), plus the safety checks, error handling and automated tests.
- **Real Groq calls:** the automated tests use a fake LLM. I checked the real model on my own machine with `python -m scripts.check_examples` and by asking questions in the UI.
- **Doesn't work:** questions that need data the CSV doesn't have (profit, customers, anything after June 2026). Those are refused on purpose.
