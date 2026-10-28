# Mini AI Business Intelligence Assistant

Ask questions about sales data in plain English and get answers backed by real SQL queries.

Built for the GET AI-Assisted Development Challenge (target effort 3–4 hours). It is a small working app, not a production system.

**Stack:** Python · FastAPI (API) · SQLite (database) · Groq LLM (text-to-SQL) · plain HTML/JS (UI) · pytest (tests)

---

## 1. Setup and run

```bash
# 1. Create a virtual environment (optional but recommended)
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. (Optional) add an LLM key – without it the app runs in "rules only" mode
cp .env.example .env              # then paste your key from https://console.groq.com/keys

# 4. Start the server
uvicorn app.main:app --reload
```

Open **http://127.0.0.1:8000** for the UI, or **http://127.0.0.1:8000/docs** for the auto-generated API docs (Swagger).

Run the tests:

```bash
pytest -v
```

Load the CSV only (prints loaded and rejected rows):

```bash
python -m app.database
```

### Example API call

```bash
curl -X POST http://127.0.0.1:8000/api/ask \
     -H "Content-Type: application/json" \
     -d '{"question": "Which region generated the highest sales?"}'
```

Response (shortened):

```json
{
  "question": "Which region generated the highest sales?",
  "method": "rule_based",
  "intent": "top_region",
  "answer": "The South region generated the highest sales: 411,200.00 from 13 orders.",
  "sql": "SELECT region, ROUND(SUM(revenue), 2) AS revenue, COUNT(*) AS orders FROM sales WHERE status = 'Completed' GROUP BY region ORDER BY revenue DESC",
  "explanation": "Revenue per region for completed orders, sorted highest first.",
  "columns": ["region", "revenue", "orders"],
  "rows": [["South", 411200.0, 13], ["North", 384450.0, 14], ["East", 346700.0, 10], ["West", 296850.0, 11]],
  "warnings": []
}
```

| Endpoint | Purpose |
|---|---|
| `GET /` | Web UI |
| `POST /api/ask` | Body `{"question": "..."}` → structured answer |
| `GET /api/health` | Status, and whether the LLM is configured |
| `GET /api/data-quality` | Rows rejected during CSV loading, with the reason |

Error codes: `400` empty or too-long question · `422` question can't be answered from this data (or the request body is malformed) · `404` question understood but no matching data (e.g. a month that isn't in the dataset) · `500` database error.

---

## 2. Architecture

```
 ┌──────────────┐   POST /api/ask    ┌────────────────────────────────────────────┐
 │  Browser UI  │ ─────────────────▶ │  FastAPI  (app/main.py)                    │
 │ static/index │ ◀───────────────── │   maps errors to HTTP status codes         │
 └──────────────┘   JSON answer      └───────────────────┬────────────────────────┘
                                                         ▼
                                     ┌────────────────────────────────────────────┐
                                     │  QA pipeline  (app/qa_service.py)          │
                                     │  1. check input                            │
                                     │  2. ROUTER: known question type?           │
                                     │       yes ─▶ rule_based.py (fixed SQL)     │
                                     │       no  ─▶ llm.py (Groq writes SQL)      │
                                     │              └▶ sql_validator.py           │
                                     │              (unsafe/failed ─▶ rules)      │
                                     │  3. run SQL  4. build answer from rows     │
                                     └───────────────────┬────────────────────────┘
                                                         ▼
                                     ┌────────────────────────────────────────────┐
                                     │  SQLite  (app/database.py, data/sales.db)  │
                                     │  tables: sales, rejected_records           │
                                     │  queries use a READ-ONLY connection        │
                                     └────────────────────────────────────────────┘
```

| File | Responsibility |
|---|---|
| `app/database.py` | Load and clean the CSV into SQLite; run read-only queries |
| `app/rule_based.py` | Keyword intent detection plus fixed SQL for the 5 example questions |
| `app/llm.py` | Groq API call: question → `{"sql", "explanation"}` JSON |
| `app/sql_validator.py` | Checks that SQL is safe before it runs |
| `app/qa_service.py` | Pipeline / router that ties everything together and writes the answer text |
| `app/main.py` | FastAPI endpoints and error handling |
| `static/index.html` | One-page UI |
| `tests/test_app.py` | Automated tests |

---

## 3. Data management

**How the data is stored.** On startup the CSV is read row by row with Python's `csv` module, validated, and inserted into SQLite (`data/sales.db`). The database is rebuilt from the CSV on every start, so the CSV stays the single source of truth.

- `sales` holds the valid rows, with proper types. Dates are converted from `DD-MM-YYYY` to ISO `YYYY-MM-DD`. Two derived columns are added:
  - `order_month` (`YYYY-MM`), which makes monthly grouping simple.
  - `revenue = quantity × unit_price − discount_amount`, computed once, so every query uses the same formula.
- `rejected_records` holds invalid rows plus the reason. They are kept for auditing, not silently dropped.

**Business rule:** revenue counts only `status = 'Completed'` orders. Returned orders are valid records, so they stay in `sales`, but every revenue query filters them out.

**Validation rules (per row):** required fields present · date parses as `DD-MM-YYYY` · quantity is an integer > 0 · unit price is a number > 0 · discount ≥ 0 and not larger than the order value · status is `Completed` or `Returned` · no duplicate transaction IDs.

**What happened with the supplied CSV (51 rows):**

| Row | Problem | Handling |
|---|---|---|
| TXN0034 | Quantity = 0 | Rejected (an order of 0 units isn't a real sale) |
| TXN0051 | Unit price missing | Rejected. I did not guess the price; see Limitations |
| TXN0033 | Status = Returned | Kept, but excluded from revenue |

Result: 49 rows loaded, 48 of them Completed. Total revenue = **1,439,200.00**. I checked this against an independent plain-Python calculation over the CSV.

**How it is queried.** Every answer comes from a SQL `SELECT` on the `sales` table. The SQL is returned in the response and shown in the UI, so the user can see exactly how each number was calculated.

---

## 4. AI integration and how wrong answers are prevented

**Approach: hybrid router (rules first for known questions, LLM for everything else).**

1. **Rule-based path (deterministic).** `detect_intent()` uses keywords to recognise the 5 example question types (total revenue, top product, monthly trend, top region, month-to-month change). Each type has a hand-written, tested SQL query. If the question contains a filter the fixed query can't apply (a region name, a product, a channel, a month…), the rule refuses to match. It doesn't return an answer to a slightly different question.
2. **LLM path (text-to-SQL).** Any other question (e.g. *"revenue by sales channel"*, *"top product in the North region"*) goes to an LLM on Groq (default `openai/gpt-oss-120b`). The prompt contains the table schema, the revenue rule, and an instruction to return `{"sql": null}` when the question can't be answered from this table. `temperature=0` and JSON mode make the output more predictable.

**Safety and correctness guards:**

- **The LLM never produces numbers.** It only writes SQL. All figures come from running that SQL on the database, and the answer text is built in Python from the result rows.
- **The SQL is validated before it runs** (`sql_validator.py`): one statement only, must start with `SELECT`, no `DROP/DELETE/UPDATE/INSERT/ALTER/CREATE/PRAGMA/ATTACH…`, no SQL comments, and the only table allowed is `sales`.
- **The database connection is read-only** (`mode=ro`). Even if a bad query got past the validator, SQLite would refuse to write. That's two layers of protection.
- **Fallbacks:** no API key, network error, invalid JSON, unsafe SQL, or a SQL syntax error all fall back to the rule-based path. If the rules don't understand the question either, the user gets a clear "can't answer" message (HTTP 422) instead of a guess.
- **Transparency:** every response includes the method used, the SQL, an explanation, and the supporting rows.
- Results are capped at 200 rows, and questions at 300 characters.

---

## 5. Testing

`pytest -v` runs **26 tests** (19 test functions; one is parametrised over 8 unsafe queries). No API key or internet connection is needed, because the LLM is switched off or replaced with a fake.

| Type | Tests |
|---|---|
| Valid questions | total revenue, top product, monthly trend, top region, March vs April change (with exact expected numbers) |
| Invalid questions | empty question (400), missing field (422), too-long question (400), unrelated question (422), filtered question the rules must not answer (422) |
| Edge cases | bad CSV rows rejected on load, a month with no data (July → 404), month parsing (`"March 2026"`, `"apr"`, `"2026-05"`, "May I know…") |
| SQL safety | DROP, DELETE, UPDATE, stacked statements, other tables, `sqlite_master`, comments, empty query are all blocked; a normal SELECT is allowed |
| LLM path (fake LLM) | generated SELECT is executed · generated `DROP TABLE` is blocked and the data stays intact · the LLM saying "unsupported" → 422 · JSON wrapped in ``` fences is parsed |

---

## 6. Known limitations

- **Rules only understand the 5 question shapes.** Without an API key, other questions get a polite "unsupported" response.
- **Keyword matching is simple.** Unusual wording (e.g. "Which SKU brought in the most money?") may not match a rule. With a key, those questions go to the LLM instead.
- **LLM answers use a generic text format** ("N rows returned. First row: …"). The table shows all the figures, but the sentence is less natural than the rule-based answers.
- **The LLM can still write a query that is valid but logically wrong** (e.g. it forgets the `Completed` filter). The validator checks safety, not business logic. The SQL is shown so the user can check it.
- **The validator is keyword/regex based, not a real SQL parser.** It also blocks the harmless SQLite `REPLACE()` string function.
- **Rejected rows are excluded.** TXN0051's price could arguably be filled in from other Laptop Stand rows (1,200), but I chose not to guess financial data.
- **Single year assumed.** A month without a year ("March") is treated as 2026, because that is the only year in the data.
- No authentication, no conversation memory, no charts. The database is rebuilt on every start.

## 7. What works / what doesn't

All 5 example questions work, in both rules-only mode and LLM mode. The LLM path has been tested with a mocked LLM. A real Groq call needs your own `GROQ_API_KEY`.
