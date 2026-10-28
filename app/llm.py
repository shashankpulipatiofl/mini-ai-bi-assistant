"""
LLM layer: asks an LLM hosted on Groq to translate a question into a SQL query.

Important design choice: the LLM ONLY writes SQL. It never writes the final
numbers. The numbers always come from running the (validated) SQL on SQLite,
so the model cannot "make up" a revenue figure.
"""

import json
import os
import re

import httpx

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
# Groq retired llama-3.3-70b-versatile in Aug 2026; gpt-oss-120b is its listed replacement.
# Change the model with the GROQ_MODEL environment variable if needed.
DEFAULT_MODEL = "openai/gpt-oss-120b"

SCHEMA_PROMPT = """You convert business questions into SQLite SELECT queries.

Database table: sales
Columns:
  transaction_id TEXT      e.g. 'TXN0001'
  order_date TEXT          ISO date 'YYYY-MM-DD'
  order_month TEXT         'YYYY-MM' (data covers 2026-01 to 2026-06)
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
- Revenue / sales means SUM(revenue) and MUST include WHERE status = 'Completed'.
- Use only the sales table. Only write ONE SELECT statement. No comments.
- Round money values with ROUND(x, 2) and give columns clear aliases.
- For "highest"/"top" questions return all groups ORDER BY value DESC (so the
  supporting figures are visible), not only LIMIT 1.
- If the question cannot be answered from this table (e.g. profit, customers,
  weather, future predictions), return sql as null.

Reply with JSON only, in this exact format:
{"sql": "<query or null>", "explanation": "<one sentence describing what the query calculates>"}
"""


class LLMError(Exception):
    """Raised when the LLM is not available or returns something unusable."""


def is_llm_enabled():
    return bool(os.getenv("GROQ_API_KEY"))


def parse_llm_reply(text):
    """Extract {"sql", "explanation"} from the model reply (tolerates ```json fences)."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM did not return valid JSON: {text[:100]}") from exc
    if not isinstance(data, dict) or "sql" not in data:
        raise LLMError("LLM reply is missing the 'sql' field")
    return data.get("sql"), data.get("explanation", "")


def generate_sql(question):
    """Call Groq and return (sql_or_None, explanation). Raises LLMError on failure."""
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise LLMError("GROQ_API_KEY is not set")

    payload = {
        "model": os.getenv("GROQ_MODEL", DEFAULT_MODEL),
        "temperature": 0,  # deterministic as possible
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": SCHEMA_PROMPT},
            {"role": "user", "content": question},
        ],
    }
    try:
        response = httpx.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=20,
        )
        response.raise_for_status()
        content = response.json()["choices"][0]["message"]["content"]
    except (httpx.HTTPError, KeyError, IndexError) as exc:
        raise LLMError(f"LLM request failed: {exc}") from exc

    return parse_llm_reply(content)
