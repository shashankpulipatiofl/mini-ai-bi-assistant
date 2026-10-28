"""
SQL validator: checks a query (usually written by the LLM) BEFORE it is executed.

Rules:
1. Must be a single statement (no ';' in the middle).
2. Must start with SELECT (read-only).
3. Must not contain any write / admin keyword (DROP, DELETE, UPDATE, ...).
4. Must not contain SQL comments (can be used to hide things).
5. May only read from the `sales` table.

This is the first line of defence. The second line is that the database
connection is opened in read-only mode (see database.run_query).
"""

import re

ALLOWED_TABLES = {"sales"}

FORBIDDEN_KEYWORDS = [
    "INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE", "REPLACE",
    "TRUNCATE", "ATTACH", "DETACH", "PRAGMA", "VACUUM", "REINDEX", "GRANT",
]


class UnsafeQueryError(ValueError):
    """Raised when a query fails validation."""


def validate_sql(sql):
    """Return a cleaned SQL string if safe, otherwise raise UnsafeQueryError."""
    if not sql or not sql.strip():
        raise UnsafeQueryError("Query is empty")

    query = sql.strip().rstrip(";").strip()

    if ";" in query:
        raise UnsafeQueryError("Multiple SQL statements are not allowed")

    if "--" in query or "/*" in query:
        raise UnsafeQueryError("SQL comments are not allowed")

    if not query.upper().startswith("SELECT"):
        raise UnsafeQueryError("Only SELECT queries are allowed")

    for keyword in FORBIDDEN_KEYWORDS:
        if re.search(rf"\b{keyword}\b", query, re.IGNORECASE):
            raise UnsafeQueryError(f"Forbidden keyword: {keyword}")

    # Every table after FROM / JOIN must be in the allow-list.
    # (Sub-queries like "FROM (SELECT ...)" start with '(' and are not matched here.)
    tables = re.findall(r"\b(?:FROM|JOIN)\s+([A-Za-z_][A-Za-z0-9_]*)", query, re.IGNORECASE)
    if not tables:
        raise UnsafeQueryError("Query must read from the sales table")
    for table in tables:
        if table.lower() not in ALLOWED_TABLES:
            raise UnsafeQueryError(f"Access to table '{table}' is not allowed")

    return query
