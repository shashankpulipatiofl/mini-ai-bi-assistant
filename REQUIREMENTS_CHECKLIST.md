# Requirements Checklist

Every requirement from the challenge document, and where it is met in this project.

## Objective

| Requirement | Status | Where |
|---|---|---|
| Business user asks questions about sales data in natural language | Done | `static/index.html`, `POST /api/ask` |
| Answers are supported by actual database queries | Done | The LLM writes SQL, which runs on SQLite (`app/qa_service.py`). The SQL and result rows are returned with every answer |
| AI coding assistants may be used | Used (Claude) | `AI_USAGE.md` |
| Not expected to be production-ready | Kept simple | One small FastAPI app, standard-library `csv` and `sqlite3` |

## Example questions (at least 4 of 5 required)

| Question | Status | Expected result |
|---|---|---|
| 1. What is the total revenue? | Supported | 1,439,200.00 |
| 2. Which product generated the highest revenue? | Supported | Monitor, 473,000.00 |
| 3. What is the monthly revenue trend? | Supported | 6 months, Jan–Jun 2026 (May is highest at 318,500.00) |
| 4. Which region generated the highest sales? | Supported | South, 411,200.00 |
| 5. How did revenue change between two months? | Supported | e.g. March 223,900.00 → April 240,700.00 (+16,800.00) |

All 5 are covered by automated tests (fake LLM) and by `scripts/check_examples.py` (real LLM).

## 1. Data Management

| Requirement | Status | Where |
|---|---|---|
| Load the supplied CSV into a database | Done | `app/database.py` → `load_csv_to_db()` creates `data/sales.db` at startup |
| Handle missing or invalid records appropriately | Done | `validate_row()`: TXN0034 (quantity 0) and TXN0051 (missing price) are rejected and stored in `rejected_records` with a reason (`GET /api/data-quality`). The returned order TXN0033 is kept but excluded from revenue |
| Explain how the data is stored and queried | Done | README section 3 |

## 2. Backend

| Requirement | Status | Where |
|---|---|---|
| Build an API that accepts a user's question | Done | `POST /api/ask` in `app/main.py` |
| Process the question and return a structured answer | Done | JSON with `question_type`, `answer`, `sql`, `explanation`, `columns`, `rows`, `row_count`, `attempts` |
| Handle invalid requests and errors | Done | 400 empty/too long · 422 malformed/unsupported/blocked · 404 no data · 503 LLM unavailable or bad key |

## 3. Analytics

| Requirement | Status | Where |
|---|---|---|
| Support at least four of the five example questions | All five | See above |
| Calculate results using database queries or deterministic logic | Done | Every number comes from a SQL query on SQLite. The LLM never calculates the figures |
| Display the supporting figures used to produce the answer | Done | The UI shows a "Supporting data" table and the SQL; the API returns `columns`/`rows` |

## 4. AI Integration

| Requirement | Status | Where |
|---|---|---|
| Use an LLM or an AI-assisted interpretation mechanism | Done | Groq `openai/gpt-oss-120b`: question router, text-to-SQL, answer writing, RAG for dataset questions (`app/llm.py`, `app/rag.py`) |
| If using text-to-SQL, validate the generated query before execution | Done | `app/sql_validator.py` runs before every query |
| Do not allow destructive database operations | Done | Validator (SELECT only, no DROP/DELETE/UPDATE…, no stacked statements, only the `sales` table) plus a read-only database connection. Tested |
| Explain how you prevent unsupported or incorrect answers | Done | README section 4 ("Guards" table) |

## 5. User Interface

| Requirement | Status | Where |
|---|---|---|
| Simple interface to enter questions and view results | Done | `static/index.html` at `http://127.0.0.1:8000` |
| Display the answer and relevant supporting data | Done | Answer, explanation, supporting table, SQL (or retrieved doc sections) |

## 6. Testing

| Requirement | Status | Where |
|---|---|---|
| At least five test cases | 34 tests | `tests/test_app.py` |
| Valid questions | Done | All 5 example questions with exact expected numbers |
| Invalid questions | Done | Empty, too long, missing field, unrelated, not answerable, no key, wrong key |
| At least one edge case | Done | Bad CSV rows, returned orders, month with no data, SQL self-correction, LLM failures |
| Document known limitations | Done | README section 6 |

## Deliverables

| Deliverable | Status | Where |
|---|---|---|
| 1. Source code in a Git repository | Done (local repo with history) | Push to GitHub before submitting |
| 2. README with setup and execution instructions | Done | `README.md` section 1 |
| 3. Short architecture diagram or explanation | Done | `README.md` section 2 |
| 4. Automated tests | Done | `tests/test_app.py`, run with `python -m pytest -v` |
| 5. Short document: AI tools used, how AI was used, problems, debugging, improvements | Done | `AI_USAGE.md` |

## Time limit

| Requirement | Status | Where |
|---|---|---|
| Explain what works, what does not, and why | Done | README section 7 |
