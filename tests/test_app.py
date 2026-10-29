"""
Automated tests. Run with:  pytest -v

The real LLM is replaced by a FAKE in these tests (pytest monkeypatch), so they:
  * run without an API key or internet,
  * are free and fast,
  * always give the same result.
The fake returns the SQL a correct LLM would write; the tests then check that
the app validates it, runs it on the real SQLite database, and returns the
right numbers. Expected numbers were checked with a separate script on the CSV.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from app import llm, rag
from app.main import app
from app.sql_validator import UnsafeQueryError, validate_sql

REAL_ROUTE_QUESTION = llm.route_question  # saved before any test replaces it

# SQL a correct LLM would generate for the 5 example questions
EXAMPLE_SQL = {
    "What is the total revenue?":
        "SELECT ROUND(SUM(revenue), 2) AS total_revenue FROM sales WHERE status = 'Completed'",
    "Which product generated the highest revenue?":
        "SELECT product, ROUND(SUM(revenue), 2) AS revenue FROM sales "
        "WHERE status = 'Completed' GROUP BY product ORDER BY revenue DESC",
    "What is the monthly revenue trend?":
        "SELECT order_month AS month, ROUND(SUM(revenue), 2) AS revenue FROM sales "
        "WHERE status = 'Completed' GROUP BY order_month ORDER BY order_month",
    "Which region generated the highest sales?":
        "SELECT region, ROUND(SUM(revenue), 2) AS revenue FROM sales "
        "WHERE status = 'Completed' GROUP BY region ORDER BY revenue DESC",
    "How did revenue change between March and April?":
        "SELECT order_month AS month, ROUND(SUM(revenue), 2) AS revenue FROM sales "
        "WHERE status = 'Completed' AND order_month IN ('2026-03', '2026-04') "
        "GROUP BY order_month ORDER BY order_month",
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    # Fake router: every question is a "data" question unless a test changes it
    monkeypatch.setattr(llm, "route_question", lambda q: ("data", "test"))
    # Fake LLM: SQL from the table above; the "answer" just echoes the first row
    monkeypatch.setattr(llm, "generate_sql",
                        lambda q, prev_sql=None, prev_err=None: (EXAMPLE_SQL.get(q), "test"))
    monkeypatch.setattr(llm, "write_answer",
                        lambda q, cols, rows: f"Answer based on {rows[0]}")
    with TestClient(app) as c:  # "with" runs startup -> loads CSV into SQLite
        yield c


def ask(client, question):
    return client.post("/api/ask", json={"question": question})


# ---------------------------------------------------------------- valid questions

def test_total_revenue(client):
    r = ask(client, "What is the total revenue?")
    assert r.status_code == 200
    body = r.json()
    assert body["rows"] == [[1439200.0]]
    assert body["answer"]                                   # answer text present
    assert set(body) >= {"answer", "sql", "columns", "rows", "explanation"}


def test_top_product(client):
    body = ask(client, "Which product generated the highest revenue?").json()
    assert body["rows"][0] == ["Monitor", 473000.0]
    assert body["row_count"] == 7                           # all products = supporting data


def test_monthly_trend(client):
    body = ask(client, "What is the monthly revenue trend?").json()
    assert [row[0] for row in body["rows"]] == [
        "2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]


def test_top_region(client):
    body = ask(client, "Which region generated the highest sales?").json()
    assert body["rows"][0] == ["South", 411200.0]


def test_month_comparison(client):
    body = ask(client, "How did revenue change between March and April?").json()
    assert body["rows"] == [["2026-03", 223900.0], ["2026-04", 240700.0]]


# ---------------------------------------------------------------- invalid questions

def test_empty_question_returns_400(client):
    assert ask(client, "   ").status_code == 400


def test_missing_question_field_returns_422(client):
    assert client.post("/api/ask", json={}).status_code == 422


def test_too_long_question_returns_400(client):
    assert ask(client, "revenue " * 100).status_code == 400


def test_unrelated_question_rejected_by_router(client, monkeypatch):
    monkeypatch.setattr(llm, "route_question", lambda q: ("unsupported", "Not about sales data."))
    r = ask(client, "What is the weather in Hyderabad?")
    assert r.status_code == 422
    assert "Not about sales data" in r.json()["detail"]


def test_sql_null_from_llm_returns_422(client):
    # Second guard: even if routed as "data", the SQL step can still say "not answerable"
    r = ask(client, "What is our profit margin?")      # fake generate_sql returns None
    assert r.status_code == 422


def test_missing_api_key_returns_503(monkeypatch):
    # No fake here: the REAL llm module is used, but without a key
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with TestClient(app) as c:
        r = ask(c, "What is the total revenue?")
    assert r.status_code == 503
    assert "GROQ_API_KEY" in r.json()["detail"]


# ---------------------------------------------------------------- SQL safety

def test_destructive_sql_from_llm_is_blocked(client, monkeypatch):
    monkeypatch.setattr(llm, "generate_sql", lambda q, *a: ("DROP TABLE sales", ""))
    r = ask(client, "Delete everything please")
    assert r.status_code == 422
    assert "blocked" in r.json()["detail"]
    # data is still there
    monkeypatch.setattr(llm, "generate_sql", lambda q, *a: (EXAMPLE_SQL["What is the total revenue?"], ""))
    assert ask(client, "What is the total revenue?").json()["rows"] == [[1439200.0]]


@pytest.mark.parametrize("bad_sql", [
    "DROP TABLE sales",
    "DELETE FROM sales",
    "UPDATE sales SET revenue = 0",
    "SELECT * FROM sales; DROP TABLE sales",
    "SELECT * FROM rejected_records",
    "SELECT name FROM sqlite_master",
    "SELECT * FROM sales -- hidden comment",
    "",
])
def test_validator_blocks_unsafe_sql(bad_sql):
    with pytest.raises(UnsafeQueryError):
        validate_sql(bad_sql)


def test_validator_allows_select():
    sql = "SELECT region, SUM(revenue) FROM sales WHERE status = 'Completed' GROUP BY region;"
    assert validate_sql(sql).endswith("GROUP BY region")


# ---------------------------------------------------------------- edge cases

def test_invalid_csv_rows_are_rejected_on_load(client):
    body = client.get("/api/data-quality").json()
    rejected = {row[0]: row[1] for row in body["rows"]}
    assert set(rejected) == {"TXN0034", "TXN0051"}   # quantity 0, missing unit price


def test_returned_orders_excluded_from_revenue(client, monkeypatch):
    monkeypatch.setattr(llm, "generate_sql", lambda q, *a: (
        "SELECT COUNT(*) AS n FROM sales WHERE status = 'Completed'", ""))
    assert ask(client, "How many completed orders?").json()["rows"] == [[48]]  # 49 loaded - 1 returned


def test_month_with_no_data_returns_404(client, monkeypatch):
    monkeypatch.setattr(llm, "generate_sql", lambda q, *a: (
        "SELECT ROUND(SUM(revenue), 2) AS revenue FROM sales "
        "WHERE status = 'Completed' AND order_month = '2026-07'", ""))
    r = ask(client, "What was the revenue in July?")
    assert r.status_code == 404                      # SUM over no rows = NULL -> no data


def test_llm_fixes_its_own_sql_error_on_retry(client, monkeypatch):
    calls = []

    def fake_generate(q, prev_sql=None, prev_err=None):
        calls.append(prev_err)
        if prev_err is None:
            return "SELECT SUM(revenu) AS total FROM sales", ""      # typo -> SQLite error
        return "SELECT SUM(revenue) AS total FROM sales WHERE status = 'Completed'", ""

    monkeypatch.setattr(llm, "generate_sql", fake_generate)
    r = ask(client, "total revenue?")
    assert r.status_code == 200
    assert r.json()["attempts"] == 2
    assert "no such column" in calls[1]              # the error was sent back to the LLM


def test_gives_up_after_two_bad_queries(client, monkeypatch):
    monkeypatch.setattr(llm, "generate_sql", lambda q, *a: ("SELECT nope FROM sales", ""))
    assert ask(client, "total revenue?").status_code == 422


def test_answer_llm_failure_still_returns_numbers(client, monkeypatch):
    def broken_answer(*args):
        raise llm.LLMError("timeout")
    monkeypatch.setattr(llm, "write_answer", broken_answer)
    r = ask(client, "What is the total revenue?")
    assert r.status_code == 200
    assert r.json()["rows"] == [[1439200.0]]


def test_parse_json_reply_handles_code_fences():
    data = llm.parse_json_reply('```json\n{"sql": "SELECT 1 FROM sales", "explanation": "x"}\n```')
    assert data["sql"] == "SELECT 1 FROM sales"


# ---------------------------------------------------------------- dataset questions (RAG)

def test_retrieval_finds_relevant_sections():
    titles = [s["title"] for s in rag.retrieve("explain the different attributes in the dataset")]
    assert titles[0] == "Columns and attributes"
    assert [s["title"] for s in rag.retrieve("what does status mean?")][0] == "Order status"
    assert rag.retrieve("how is revenue calculated?")[0]["title"] == "Revenue definition and business rules"
    assert rag.retrieve("what is the weather today") == []          # nothing relevant


def test_dataset_question_answered_from_docs(client, monkeypatch):
    monkeypatch.setattr(llm, "route_question", lambda q: ("dataset_info", "about columns"))
    seen_prompts = []

    def fake_call_llm(system_prompt, user_message):
        seen_prompts.append(user_message)
        return {"answer": "The dataset has 10 columns such as product, region and status."}

    monkeypatch.setattr(llm, "call_llm", fake_call_llm)
    r = ask(client, "What attributes are in the dataset?")
    assert r.status_code == 200
    body = r.json()
    assert body["question_type"] == "dataset_info"
    assert body["sql"] is None
    assert body["rows"][0][0] == "Columns and attributes"           # retrieved section shown
    assert "transaction_id" in seen_prompts[0]                       # docs were put in the prompt


def test_dataset_question_not_in_docs_returns_422(client, monkeypatch):
    monkeypatch.setattr(llm, "route_question", lambda q: ("dataset_info", ""))
    r = ask(client, "Who designed the xyzzy logo?")                  # retrieves nothing
    assert r.status_code == 422


def test_llm_says_docs_do_not_contain_answer(client, monkeypatch):
    monkeypatch.setattr(llm, "route_question", lambda q: ("dataset_info", ""))
    monkeypatch.setattr(llm, "call_llm", lambda *a: {"answer": None})
    assert ask(client, "What does the status column mean for customers?").status_code == 422


def test_router_bad_reply_returns_503(client, monkeypatch):
    # Use the REAL route_question, but make the LLM reply with an unknown type
    monkeypatch.setattr(llm, "route_question", REAL_ROUTE_QUESTION)
    monkeypatch.setattr(llm, "call_llm", lambda *a: {"type": "banana"})
    assert ask(client, "What is the total revenue?").status_code == 503


def test_wrong_api_key_gives_clear_message(monkeypatch):
    # Simulate Groq answering 401 Unauthorized (wrong / revoked key)
    monkeypatch.setenv("GROQ_API_KEY", "wrong-key")
    fake_response = httpx.Response(401, request=httpx.Request("POST", llm.GROQ_URL))
    monkeypatch.setattr(llm.httpx, "post", lambda *a, **k: fake_response)
    with TestClient(app) as c:
        r = ask(c, "What is the total revenue?")
    assert r.status_code == 503
    assert "rejected the API key" in r.json()["detail"]
