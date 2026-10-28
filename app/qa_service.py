"""
Question-answering pipeline (the "brain" of the app).

    question
       |
       v
  1. basic input checks (empty / too long)
       |
       v
  2. LLM (Groq) writes SQL  --(no key / error / unsafe SQL)-->  rule-based fallback
       |                                                              |
       v                                                              v
  3. SQL validator (SELECT-only, sales table only)          fixed, hand-written SQL
       |                                                              |
       +------------------------------+-------------------------------+
                                      v
  4. run query on SQLite (read-only connection)
                                      v
  5. build answer text from the query result (no numbers are invented)
"""

import logging
import sqlite3
from datetime import datetime

from app import database, llm, rule_based
from app.sql_validator import UnsafeQueryError, validate_sql

logger = logging.getLogger(__name__)

MAX_QUESTION_LENGTH = 300


class InvalidQuestionError(ValueError):
    """Question is empty / malformed -> HTTP 400."""


class UnsupportedQuestionError(ValueError):
    """We cannot answer this question from the data -> HTTP 422."""


class NoDataError(ValueError):
    """Question understood, but the data has no matching rows -> HTTP 404."""


def money(value):
    return f"{value:,.2f}"


def month_name(ym):
    """'2026-03' -> 'March 2026'"""
    try:
        return datetime.strptime(ym, "%Y-%m").strftime("%B %Y")
    except (TypeError, ValueError):
        return str(ym)


# ---------------------------------------------------------------- answers

def build_rule_answer(intent, columns, rows, question):
    """Human-readable answer for the 5 known intents (deterministic templates)."""
    if intent == "total_revenue":
        total, orders = rows[0]
        return f"Total revenue is {money(total)} from {orders} completed orders."

    if intent == "top_product":
        product, revenue, units = rows[0]
        return f"{product} generated the highest revenue: {money(revenue)} ({units} units sold)."

    if intent == "top_region":
        region, revenue, orders = rows[0]
        return f"The {region} region generated the highest sales: {money(revenue)} from {orders} orders."

    if intent == "monthly_trend":
        first, last = rows[0], rows[-1]
        best = max(rows, key=lambda r: r[1])
        direction = "up" if last[1] > first[1] else "down"
        return (
            f"Revenue went {direction} from {money(first[1])} in {month_name(first[0])} "
            f"to {money(last[1])} in {month_name(last[0])}. "
            f"Best month: {month_name(best[0])} ({money(best[1])})."
        )

    if intent == "month_comparison":
        m1, m2 = rule_based.extract_months(question)[:2]
        by_month = {r[0]: r[1] for r in rows}
        for m in (m1, m2):
            if m not in by_month:
                raise NoDataError(f"No completed sales found for {month_name(m)}.")
        r1, r2 = by_month[m1], by_month[m2]
        diff = r2 - r1
        pct = (diff / r1 * 100) if r1 else 0
        word = "increased" if diff > 0 else "decreased" if diff < 0 else "did not change"
        return (
            f"Revenue {word} from {money(r1)} in {month_name(m1)} to {money(r2)} in "
            f"{month_name(m2)}: a change of {money(diff)} ({pct:+.1f}%)."
        )

    return f"{len(rows)} row(s) returned."


def build_generic_answer(columns, rows):
    """Answer text for LLM-generated queries: just describe the result, no invention."""
    if len(rows) == 1:
        parts = [f"{c} = {money(v) if isinstance(v, float) else v}" for c, v in zip(columns, rows[0])]
        return "Result: " + ", ".join(parts)
    first = ", ".join(
        f"{c} = {money(v) if isinstance(v, float) else v}" for c, v in zip(columns, rows[0])
    )
    return f"{len(rows)} rows returned. First row: {first}. See the table for all figures."


def has_data(rows):
    return bool(rows) and not all(v is None for v in rows[0])


# ---------------------------------------------------------------- pipeline

def answer_with_rules(question):
    intent = rule_based.detect_intent(question)
    if intent is None:
        raise UnsupportedQuestionError(
            "Sorry, I can't answer that from the sales data. Try questions about total revenue, "
            "top product, top region, monthly trend, or comparing two months."
        )
    try:
        sql, params, explanation = rule_based.build_query(intent, question)
    except ValueError as exc:
        raise UnsupportedQuestionError(str(exc)) from exc

    columns, rows = database.run_query(sql, params)
    if not has_data(rows):
        raise NoDataError("No matching sales data found for this question.")

    return {
        "method": "rule_based",
        "intent": intent,
        "sql": sql,
        "params": list(params),
        "explanation": explanation,
        "answer": build_rule_answer(intent, columns, rows, question),
        "columns": columns,
        "rows": rows,
    }


def answer_with_llm(question):
    sql, explanation = llm.generate_sql(question)
    if not sql:
        raise UnsupportedQuestionError(
            "The AI model says this question cannot be answered from the sales data."
        )
    safe_sql = validate_sql(sql)  # raises UnsafeQueryError
    columns, rows = database.run_query(safe_sql)
    if not has_data(rows):
        raise NoDataError("No matching sales data found for this question.")
    return {
        "method": "llm",
        "intent": None,
        "sql": safe_sql,
        "params": [],
        "explanation": explanation,
        "answer": build_generic_answer(columns, rows),
        "columns": columns,
        "rows": rows,
    }


def answer_question(question):
    """Main entry point used by the API."""
    if question is None or not question.strip():
        raise InvalidQuestionError("Question cannot be empty.")
    question = question.strip()
    if len(question) > MAX_QUESTION_LENGTH:
        raise InvalidQuestionError(f"Question is too long (max {MAX_QUESTION_LENGTH} characters).")

    warnings = []

    # Router:
    #  - If the question exactly matches one of the 5 known question types
    #    (and has no extra filters), use the deterministic rule-based answer.
    #  - Otherwise ask the LLM to write SQL (e.g. "revenue by sales channel",
    #    "top product in the North region").
    #  - If the LLM is not configured or fails, fall back to the rules, which
    #    will say "unsupported" rather than give a wrong answer.
    known_intent = rule_based.detect_intent(question)

    if llm.is_llm_enabled() and known_intent is None:
        try:
            result = answer_with_llm(question)
            result["warnings"] = warnings
            return {"question": question, **result}
        except UnsupportedQuestionError:
            raise
        except NoDataError:
            raise
        except (llm.LLMError, UnsafeQueryError, sqlite3.Error) as exc:
            logger.warning("LLM path failed, falling back to rules: %s", exc)
            warnings.append(f"AI query was not used ({exc}); fell back to rule-based answer.")

    result = answer_with_rules(question)
    result["warnings"] = warnings
    return {"question": question, **result}
