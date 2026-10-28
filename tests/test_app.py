"""
Automated tests. Run with:  pytest -v

The LLM is switched OFF (or replaced by a fake) in every test, so the tests
are fast, free, and do not depend on the internet.
Expected numbers were checked by hand / with a separate script against the CSV.
"""

import pytest
from fastapi.testclient import TestClient

from app import llm
from app.main import app
from app.rule_based import detect_intent, extract_months
from app.sql_validator import UnsafeQueryError, validate_sql


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)  # rules-only mode
    with TestClient(app) as c:  # "with" runs startup -> loads CSV into SQLite
        yield c


def ask(client, question):
    return client.post("/api/ask", json={"question": question})


# ---------------------------------------------------------------- valid questions

def test_total_revenue(client):
    r = ask(client, "What is the total revenue?")
    assert r.status_code == 200
    body = r.json()
    assert body["rows"][0][0] == 1439200.0          # total revenue
    assert body["rows"][0][1] == 48                 # completed orders
    assert "1,439,200.00" in body["answer"]
    assert "status = 'Completed'" in body["sql"]   # returned orders excluded


def test_top_product(client):
    r = ask(client, "Which product generated the highest revenue?")
    assert r.status_code == 200
    body = r.json()
    assert body["rows"][0][0] == "Monitor"
    assert body["rows"][0][1] == 473000.0
    assert len(body["rows"]) == 7                   # all products shown as supporting data


def test_monthly_trend(client):
    r = ask(client, "What is the monthly revenue trend?")
    assert r.status_code == 200
    months = [row[0] for row in r.json()["rows"]]
    assert months == ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]


def test_top_region(client):
    r = ask(client, "Which region generated the highest sales?")
    assert r.status_code == 200
    assert r.json()["rows"][0][:2] == ["South", 411200.0]


def test_month_comparison(client):
    r = ask(client, "How did revenue change between March and April?")
    assert r.status_code == 200
    body = r.json()
    assert "increased" in body["answer"]
    assert "16,800.00" in body["answer"]             # 240,700 - 223,900
    assert body["params"] == ["2026-03", "2026-04"]


# ---------------------------------------------------------------- invalid questions

def test_empty_question_returns_400(client):
    assert ask(client, "   ").status_code == 400


def test_missing_question_field_returns_422(client):
    assert client.post("/api/ask", json={}).status_code == 422


def test_too_long_question_returns_400(client):
    assert ask(client, "revenue " * 100).status_code == 400


def test_unrelated_question_returns_422(client):
    r = ask(client, "What is the weather in Hyderabad?")
    assert r.status_code == 422


def test_filtered_question_not_answered_by_rules(client):
    # Rules can't filter by region, so they must NOT return the overall top product.
    r = ask(client, "Which product had the highest revenue in the North region?")
    assert r.status_code == 422


# ---------------------------------------------------------------- edge cases

def test_invalid_rows_are_rejected_on_load(client):
    body = client.get("/api/data-quality").json()
    rejected = {row[0]: row[1] for row in body["rows"]}
    assert set(rejected) == {"TXN0034", "TXN0051"}   # quantity 0, missing unit price
    assert "Quantity" in rejected["TXN0034"]
    assert "unit price" in rejected["TXN0051"]


def test_month_with_no_data_returns_404(client):
    r = ask(client, "Compare revenue between June and July")
    assert r.status_code == 404
    assert "July" in r.json()["detail"]


def test_month_extraction():
    assert extract_months("Compare March 2026 vs apr") == ["2026-03", "2026-04"]
    assert extract_months("change from 2026-05 to 2026-02") == ["2026-05", "2026-02"]
    assert detect_intent("May I know the total revenue?") == "total_revenue"


# ---------------------------------------------------------------- SQL safety

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


# ---------------------------------------------------------------- LLM path (fake LLM)

def test_llm_generated_sql_is_executed(client, monkeypatch):
    monkeypatch.setattr(llm, "is_llm_enabled", lambda: True)
    monkeypatch.setattr(llm, "generate_sql", lambda q: (
        "SELECT sales_channel, ROUND(SUM(revenue), 2) AS revenue FROM sales "
        "WHERE status = 'Completed' GROUP BY sales_channel ORDER BY revenue DESC",
        "Revenue per sales channel.",
    ))
    r = ask(client, "Revenue split by sales channel?")
    assert r.status_code == 200
    body = r.json()
    assert body["method"] == "llm"
    assert {row[0] for row in body["rows"]} == {"Online", "Retail"}


def test_llm_destructive_sql_is_blocked(client, monkeypatch):
    monkeypatch.setattr(llm, "is_llm_enabled", lambda: True)
    monkeypatch.setattr(llm, "generate_sql", lambda q: ("DROP TABLE sales", ""))
    r = ask(client, "Delete everything please")
    # Unsafe SQL is never run -> falls back to rules -> rules don't understand -> 422
    assert r.status_code == 422
    # Table still exists and data is intact
    assert ask(client, "What is the total revenue?").json()["rows"][0][0] == 1439200.0


def test_llm_says_unsupported(client, monkeypatch):
    monkeypatch.setattr(llm, "is_llm_enabled", lambda: True)
    monkeypatch.setattr(llm, "generate_sql", lambda q: (None, "Profit is not in the data."))
    assert ask(client, "What is our profit margin?").status_code == 422


def test_parse_llm_reply_handles_code_fences():
    sql, _ = llm.parse_llm_reply('```json\n{"sql": "SELECT 1 FROM sales", "explanation": "x"}\n```')
    assert sql == "SELECT 1 FROM sales"
