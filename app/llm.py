"""
LLM layer: talks to an LLM hosted on Groq (OpenAI-compatible API).

The LLM is used for two jobs:
  1. generate_sql()  -> turn the user's question into ONE SQLite SELECT query
  2. write_answer()  -> turn the query RESULT ROWS into a short English answer

Important: the LLM never calculates numbers itself. The numbers come from
running the SQL on the database; the LLM only describes those rows.
"""

import json
import os
import re

import httpx

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Groq retired llama-3.3-70b-versatile in Aug 2026; gpt-oss-120b is its listed replacement.
# Change the model with the GROQ_MODEL environment variable if needed.
DEFAULT_MODEL = "openai/gpt-oss-120b"

SQL_PROMPT = """You convert business questions into SQLite SELECT queries.

Database table: sales
Columns:
  transaction_id TEXT      e.g. 'TXN0001'
  order_date TEXT          ISO date 'YYYY-MM-DD'
  order_month TEXT         'YYYY-MM'. Data covers 2026-01 to 2026-06 only.
  product TEXT             Laptop Stand, Wireless Mouse, Office Chair, Monitor, Keyboard, Desk, USB-C Hub
  category TEXT            Accessories, Furniture, Electronics
  region TEXT              North, South, East, West
  sales_channel TEXT       Online, Retail
  quantity INTEGER
  unit_price REAL
  discount_amount REAL
  status TEXT              'Completed' or 'Returned'
  revenue REAL             already calculated as quantity * unit_price - discount_amount

Rules:
- "Revenue" or "sales" means SUM(revenue) and MUST include WHERE status = 'Completed'.
- Use only the sales table. Write exactly ONE SELECT statement. No comments.
- Round money values with ROUND(x, 2) and give every column a clear alias.
- For "highest" / "top" / "best" questions, return ALL groups ordered DESC (not LIMIT 1),
  so the user can see the supporting figures.
- For a trend, group by order_month and order by order_month.
- For a change between two months, return one row per month (month, revenue) for
  those two months only. Months without a year mean 2026 (e.g. March -> '2026-03').
- If the question cannot be answered from this table (e.g. profit, customers, weather,
  forecasts) or is not a question about the data, set "sql" to null.

Reply with JSON only, in this exact format:
{"sql": "<query or null>", "explanation": "<one sentence: what the query calculates>"}
"""

ANSWER_PROMPT = """You are a business analyst. Answer the user's question in 1-3 sentences
using ONLY the query result given to you.
- Do not invent or estimate any number that is not in the result, except simple
  differences or percentages calculated from numbers in the result.
- Format money with thousands separators and 2 decimals (e.g. 1,439,200.00).
- If the result is empty, say that no matching data was found.

Reply with JSON only: {"answer": "<your answer>"}
"""


class LLMError(Exception):
    """The LLM is not available or returned something unusable."""


def is_llm_enabled():
    return bool(os.getenv("GROQ_API_KEY"))


def parse_json_reply(text):
    """Parse the model's JSON reply (also works if it is wrapped in ```json fences)."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM did not return valid JSON: {text[:100]}") from exc
    if not isinstance(data, dict):
        raise LLMError("LLM reply is not a JSON object")
    return data


def call_llm(system_prompt, user_message):
    """Send one chat request to Groq and return the parsed JSON reply."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise LLMError("GROQ_API_KEY is not set. Add it to the .env file.")

    payload = {
        "model": os.getenv("GROQ_MODEL", DEFAULT_MODEL),
        "temperature": 0,  # as deterministic as possible
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ],
    }
    try:
        response = httpx.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        raise LLMError(f"LLM request failed: {exc}") from exc

    return parse_json_reply(content)


def generate_sql(question, previous_sql=None, previous_error=None):
    """
    Ask the LLM for a SQL query. Returns (sql_or_None, explanation).
    If a previous attempt failed, the failed SQL and the error are sent back
    so the model can correct itself.
    """
    message = f"Question: {question}"
    if previous_error:
        message += (
            f"\n\nYour previous query:\n{previous_sql}\n"
            f"failed with this error:\n{previous_error}\n"
            "Please return a corrected query."
        )
    data = call_llm(SQL_PROMPT, message)
    if "sql" not in data:
        raise LLMError("LLM reply is missing the 'sql' field")
    return data.get("sql"), data.get("explanation", "")


def write_answer(question, columns, rows):
    """Ask the LLM to describe the query result in plain English."""
    result = {"columns": columns, "rows": rows}
    message = f"Question: {question}\n\nQuery result (JSON):\n{json.dumps(result)}"
    data = call_llm(ANSWER_PROMPT, message)
    answer = data.get("answer")
    if not answer:
        raise LLMError("LLM reply is missing the 'answer' field")
    return answer
