"""
Question-answering pipeline.

    question
       |
  1. check input (empty / too long)                         -> 400
       |
  2. ROUTER: LLM classifies the question
       |-- "unsupported"  ---------------------------------> 422
       |
       |-- "dataset_info" (e.g. "what columns are there?")
       |       RAG: retrieve relevant sections of docs/data_dictionary.md,
       |       LLM answers ONLY from those sections         (nothing found -> 422)
       |
       |-- "data" (e.g. "which region had the highest sales?")
               a. LLM writes a SQL query  (sql = null       -> 422)
               b. validate the SQL (SELECT only, sales only) -> 422 if unsafe
               c. run it on SQLite (read-only); on a SQL error send the error
                  back to the LLM and try ONE more time (self-correction)
               d. empty result                               -> 404
               e. LLM writes a short answer from the result rows
       |
  3. return a structured JSON answer
"""

import logging
import sqlite3

from app import database, llm, rag
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


# ---------------------------------------------------------------- dataset_info (RAG)

def answer_dataset_question(question):
    answer, sections = rag.answer_from_docs(question)
    if not answer:
        raise UnsupportedQuestionError(
            "I couldn't find this in the dataset documentation."
        )
    return {
        "question": question,
        "question_type": "dataset_info",
        "answer": answer,
        "sql": None,
        "explanation": "Answered from the data dictionary (docs/data_dictionary.md) "
                       "using the retrieved sections below.",
        "columns": ["section", "content"],
        "rows": [[s["title"], s["text"]] for s in sections],
        "row_count": len(sections),
        "attempts": 1,
    }


# ---------------------------------------------------------------- data (text-to-SQL)

def get_sql_and_rows(question):
    """Ask the LLM for SQL, validate it, run it (retry once on a SQL error)."""
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


def answer_data_question(question):
    sql, explanation, columns, rows, attempts = get_sql_and_rows(question)

    if not has_data(rows):
        raise NoDataError("No matching sales data was found for this question.")

    # If writing the sentence fails we still have correct numbers,
    # so return a plain summary instead of an error.
    try:
        answer = llm.write_answer(question, columns, rows)
    except llm.LLMError as exc:
        logger.warning("Answer generation failed: %s", exc)
        answer = f"The query returned {len(rows)} row(s). See the supporting data below."

    return {
        "question": question,
        "question_type": "data",
        "answer": answer,
        "sql": sql,
        "explanation": explanation,
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "attempts": attempts,
    }


# ---------------------------------------------------------------- entry point

def answer_question(question):
    """Main entry point used by the API. Returns a dict (the structured answer)."""
    if question is None or not question.strip():
        raise InvalidQuestionError("Question cannot be empty.")
    question = question.strip()
    if len(question) > MAX_QUESTION_LENGTH:
        raise InvalidQuestionError(f"Question is too long (max {MAX_QUESTION_LENGTH} characters).")

    question_type, reason = llm.route_question(question)
    logger.info("Question type: %s (%s)", question_type, reason)

    if question_type == "unsupported":
        raise UnsupportedQuestionError(
            f"This question can't be answered from the sales data. {reason}".strip()
        )
    if question_type == "dataset_info":
        return answer_dataset_question(question)
    return answer_data_question(question)
