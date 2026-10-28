"""
Rule-based (keyword) interpreter.

Used when:
  * no LLM API key is configured, or
  * the LLM call fails / returns an unsafe or broken query.

It maps a question to one of 5 known "intents", each with a fixed,
hand-written SQL query. Because the SQL is fixed, the answers are fully
deterministic and easy to test.
"""

import re

REVENUE_FILTER = "status = 'Completed'"

MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "jun": 6, "jul": 7, "aug": 8,
    "sep": 9, "sept": 9, "oct": 10, "nov": 11, "dec": 12,
}
DEFAULT_YEAR = 2026  # the dataset only contains 2026


def extract_months(question):
    """
    Find month mentions in the question, in the order they appear.
    Accepts 'March', 'mar', 'March 2026' and '2026-03'. Returns ['2026-03', ...].
    """
    text = question.lower()
    found = []  # list of (position, 'YYYY-MM')

    for match in re.finditer(r"\b(20\d{2})-(0[1-9]|1[0-2])\b", text):
        found.append((match.start(), f"{match.group(1)}-{match.group(2)}"))

    for match in re.finditer(r"\b([a-z]+)\b(?:\s+(20\d{2}))?", text):
        word, year = match.group(1), match.group(2)
        if word in MONTHS:
            # "may" is also a normal English word ("may I know...") - only treat it
            # as a month when it is not the first word of the question.
            if word == "may" and match.start() == 0:
                continue
            found.append((match.start(), f"{year or DEFAULT_YEAR}-{MONTHS[word]:02d}"))

    found.sort()
    months = []
    for _, m in found:
        if m not in months:
            months.append(m)
    return months


# Words that mean the user wants a FILTER (e.g. "in the North region").
# The fixed rule queries cannot filter, so if one of these appears we do NOT
# use the rule - otherwise we would return an answer to a different question.
REGION_WORDS = ["north", "south", "east", "west"]
PRODUCT_WORDS = ["laptop", "stand", "mouse", "chair", "monitor", "keyboard", "desk", "hub", "usb"]
OTHER_FILTER_WORDS = ["accessories", "furniture", "electronics", "online", "retail", "returned"]


def _mentions(text, words):
    return any(re.search(rf"\b{w}\b", text) for w in words)


def detect_intent(question):
    """Return the intent name for a question, or None if not understood."""
    q = question.lower()
    has = lambda *words: any(w in q for w in words)

    mentions_region = _mentions(q, REGION_WORDS)
    mentions_product = _mentions(q, PRODUCT_WORDS)
    mentions_other = _mentions(q, OTHER_FILTER_WORDS)
    months = extract_months(question)
    any_filter = mentions_region or mentions_product or mentions_other

    if has("change", "compare", "comparison", "difference", " vs", "versus", "growth") \
            and len(months) >= 2:
        return None if any_filter else "month_comparison"
    if has("trend", "monthly", "by month", "per month", "each month", "month wise", "month-wise"):
        return None if (any_filter or months) else "monthly_trend"
    if has("product", "item") and has("highest", "top", "most", "best", "maximum", "max"):
        return None if (mentions_region or mentions_other or months) else "top_product"
    if has("region", "area", "zone") and has("highest", "top", "most", "best", "maximum", "max"):
        return None if (mentions_product or mentions_other or months) else "top_region"
    if has("total revenue", "total sales", "overall revenue", "overall sales",
           "how much revenue", "revenue in total", "total income"):
        return None if (any_filter or months) else "total_revenue"
    return None


def build_query(intent, question):
    """
    Return (sql, params, explanation) for an intent.
    Raises ValueError if the question is missing information (e.g. months).
    """
    if intent == "total_revenue":
        return (
            f"SELECT ROUND(SUM(revenue), 2) AS total_revenue, COUNT(*) AS orders "
            f"FROM sales WHERE {REVENUE_FILTER}",
            (),
            "Sum of revenue (quantity x unit price - discount) for completed orders.",
        )

    if intent == "top_product":
        return (
            f"SELECT product, ROUND(SUM(revenue), 2) AS revenue, SUM(quantity) AS units_sold "
            f"FROM sales WHERE {REVENUE_FILTER} GROUP BY product ORDER BY revenue DESC",
            (),
            "Revenue per product for completed orders, sorted highest first.",
        )

    if intent == "top_region":
        return (
            f"SELECT region, ROUND(SUM(revenue), 2) AS revenue, COUNT(*) AS orders "
            f"FROM sales WHERE {REVENUE_FILTER} GROUP BY region ORDER BY revenue DESC",
            (),
            "Revenue per region for completed orders, sorted highest first.",
        )

    if intent == "monthly_trend":
        return (
            f"SELECT order_month AS month, ROUND(SUM(revenue), 2) AS revenue, COUNT(*) AS orders "
            f"FROM sales WHERE {REVENUE_FILTER} GROUP BY order_month ORDER BY order_month",
            (),
            "Revenue per month for completed orders, in date order.",
        )

    if intent == "month_comparison":
        months = extract_months(question)
        if len(months) < 2:
            raise ValueError("Please mention two months to compare, e.g. 'March vs April'.")
        m1, m2 = months[0], months[1]
        return (
            f"SELECT order_month AS month, ROUND(SUM(revenue), 2) AS revenue, COUNT(*) AS orders "
            f"FROM sales WHERE {REVENUE_FILTER} AND order_month IN (?, ?) "
            f"GROUP BY order_month ORDER BY order_month",
            (m1, m2),
            f"Revenue for {m1} and {m2} (completed orders), then the difference.",
        )

    raise ValueError(f"Unknown intent: {intent}")
