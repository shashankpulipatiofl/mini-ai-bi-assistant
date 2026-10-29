"""
Question-answering pipeline.

    question
       |
  1. check input (empty / too long)                      -> 400
       |
  2. LLM writes a SQL query  (sql = null -> unsupported) -> 422
       |
  3. validate the SQL (SELECT only, sales table only)    -> 422 if unsafe
       |
  4. run it on SQLite (read-only connection)
       |   if SQLite reports an error, send the error back to the LLM
       |   and try ONE more time (self-correction)
       |
  5. empty result                                        -> 404
       |
  6. LLM writes a short answer from the result rows
       |
  7. return a structured JSON answer: answer + SQL + supporting rows
"""

import logging
import sqlite3

from app import database, llm
from app.sql_validator import UnsafeQueryError, validate_sql

logger = logging.getLogger(__name__)

MAX_QUESTION_LENGTH = 300
MAX_ATTEMPTS = 2  # first try + one retry after a SQL error


class InvalidQuestionError(ValueError):
    """Question is empty / malformed -> HTTP 400."""


class UnsupportedQuestionError(ValueError):
    """Cannot be answered safely from the data -> HTTP 422."""


class NoDataError(ValueError):
    """Valid query, but no matching rows -> HTTP 404."""


def has_data(rows):
    """False if there are no rows, or the only row is all NULLs (e.g. SUM of nothing)."""
    return bool(rows) and not all(value is None for value in rows[0])


def get_sql_and_rows(question):
    """Steps 2-4: ask the LLM for SQL, validate it, run it (retry once on SQL error)."""
    previous_sql, previous_error = None, None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        sql, explanation = llm.generate_sql(question, previous_sql, previous_error)

        if not sql:
            raise UnsupportedQuestionError(
                "This question can't be answered from the sales data. "
                f"{explanation}".strip()
            )

        try:
            safe_sql = validate_sql(sql)
        except UnsafeQueryError as exc:
            # Never retry or run an unsafe query - stop here.
            logger.warning("Blocked unsafe SQL from LLM: %s (%s)", sql, exc)
            raise UnsupportedQuestionError(f"The generated query was blocked: {exc}") from exc

        try:
            columns, rows = database.run_query(safe_sql)
            return safe_sql, explanation, columns, rows, attempt
        except sqlite3.Error as exc:
            logger.warning("Attempt %s: SQL error %s for query %s", attempt, exc, safe_sql)
            previous_sql, previous_error = safe_sql, str(exc)

    raise UnsupportedQuestionError(
        f"Could not build a working query for this question (last error: {previous_error})."
    )


def answer_question(question):
    """Main entry point used by the API. Returns a dict (the structured answer)."""
    # Step 1: input checks
    if question is None or not question.strip():
        raise InvalidQuestionError("Question cannot be empty.")
    question = question.strip()
    if len(question) > MAX_QUESTION_LENGTH:
        raise InvalidQuestionError(f"Question is too long (max {MAX_QUESTION_LENGTH} characters).")

    # Steps 2-4
    sql, explanation, columns, rows, attempts = get_sql_and_rows(question)

    # Step 5
    if not has_data(rows):
        raise NoDataError("No matching sales data was found for this question.")

    # Step 6: the LLM describes the rows. If that call fails we still have
    # correct numbers, so return a plain summary instead of an error.
    try:
        answer = llm.write_answer(question, columns, rows)
    except llm.LLMError as exc:
        logger.warning("Answer generation failed: %s", exc)
        answer = f"The query returned {len(rows)} row(s). See the supporting data below."

    # Step 7
    return {
        "question": question,
        "answer": answer,
        "sql": sql,
        "explanation": explanation,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "attempts": attempts,
    }
