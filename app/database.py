"""
Data layer: loads the sales CSV into a SQLite database and runs read-only queries.

How data is stored
------------------
* `sales`            -> every VALID row from the CSV (cleaned + typed).
                        Extra column `revenue` = quantity * unit_price - discount_amount.
                        Extra column `order_month` = 'YYYY-MM' (makes monthly grouping easy).
* `rejected_records` -> rows that failed validation, together with the reason.
                        We keep them (instead of silently dropping) so they can be audited.

Business rule: only rows with status = 'Completed' count as revenue.
Returned orders stay in `sales` (they are valid records) but are filtered out in queries.
"""

import csv
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
CSV_PATH = BASE_DIR / "data" / "sales_data.csv"
DB_PATH = BASE_DIR / "data" / "sales.db"

VALID_STATUSES = {"Completed", "Returned"}

CREATE_TABLES_SQL = """
DROP TABLE IF EXISTS sales;
DROP TABLE IF EXISTS rejected_records;

CREATE TABLE sales (
    transaction_id   TEXT PRIMARY KEY,
    order_date       TEXT NOT NULL,      -- ISO format YYYY-MM-DD
    order_month      TEXT NOT NULL,      -- YYYY-MM
    product          TEXT NOT NULL,
    category         TEXT NOT NULL,
    region           TEXT NOT NULL,
    sales_channel    TEXT NOT NULL,
    quantity         INTEGER NOT NULL CHECK (quantity > 0),
    unit_price       REAL NOT NULL CHECK (unit_price > 0),
    discount_amount  REAL NOT NULL CHECK (discount_amount >= 0),
    status           TEXT NOT NULL,
    revenue          REAL NOT NULL       -- quantity * unit_price - discount_amount
);

CREATE TABLE rejected_records (
    transaction_id TEXT,
    raw_row        TEXT,
    reason         TEXT
);
"""


def validate_row(row, seen_ids):
    """
    Check one CSV row. Returns (clean_row_dict, None) if valid,
    or (None, reason) if the row should be rejected.
    """
    txn_id = (row.get("Transaction_Id") or "").strip()
    if not txn_id:
        return None, "Missing transaction id"
    if txn_id in seen_ids:
        return None, "Duplicate transaction id"

    # Required text fields must not be empty
    for field in ["Order_date", "Product", "Category", "Region", "Sales_channel", "Status"]:
        if not (row.get(field) or "").strip():
            return None, f"Missing {field}"

    # Date is in DD-MM-YYYY format in the CSV
    try:
        date = datetime.strptime(row["Order_date"].strip(), "%d-%m-%Y")
    except ValueError:
        return None, f"Invalid date '{row['Order_date']}'"

    # Numbers
    try:
        quantity = int(row["Quantity"])
    except (ValueError, TypeError):
        return None, "Missing or invalid quantity"
    try:
        unit_price = float(row["Unit_price"])
    except (ValueError, TypeError):
        return None, "Missing or invalid unit price"
    try:
        discount = float(row["Discount_amount"] or 0)
    except ValueError:
        return None, "Invalid discount amount"

    if quantity <= 0:
        return None, "Quantity must be greater than 0"
    if unit_price <= 0:
        return None, "Unit price must be greater than 0"
    if discount < 0:
        return None, "Discount cannot be negative"
    if discount > quantity * unit_price:
        return None, "Discount is larger than the order value"

    status = row["Status"].strip()
    if status not in VALID_STATUSES:
        return None, f"Unknown status '{status}'"

    clean = {
        "transaction_id": txn_id,
        "order_date": date.strftime("%Y-%m-%d"),
        "order_month": date.strftime("%Y-%m"),
        "product": row["Product"].strip(),
        "category": row["Category"].strip(),
        "region": row["Region"].strip(),
        "sales_channel": row["Sales_channel"].strip(),
        "quantity": quantity,
        "unit_price": unit_price,
        "discount_amount": discount,
        "status": status,
        "revenue": quantity * unit_price - discount,
    }
    return clean, None


def load_csv_to_db(csv_path=CSV_PATH, db_path=DB_PATH):
    """Create the database from the CSV. Returns a small summary dict."""
    conn = sqlite3.connect(db_path)
    conn.executescript(CREATE_TABLES_SQL)

    loaded, rejected = 0, []
    seen_ids = set()

    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            clean, reason = validate_row(row, seen_ids)
            if clean:
                seen_ids.add(clean["transaction_id"])
                conn.execute(
                    """INSERT INTO sales VALUES (:transaction_id, :order_date, :order_month,
                       :product, :category, :region, :sales_channel, :quantity,
                       :unit_price, :discount_amount, :status, :revenue)""",
                    clean,
                )
                loaded += 1
            else:
                conn.execute(
                    "INSERT INTO rejected_records VALUES (?, ?, ?)",
                    (row.get("Transaction_Id"), str(dict(row)), reason),
                )
                rejected.append({"transaction_id": row.get("Transaction_Id"), "reason": reason})

    conn.commit()
    conn.close()
    return {"loaded": loaded, "rejected": rejected}


def run_query(sql, params=(), db_path=DB_PATH):
    """
    Run a SELECT query and return (columns, rows).
    The connection is opened in READ-ONLY mode (mode=ro), so even if a bad
    query slipped past the validator, SQLite itself would refuse to modify data.
    """
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        cursor = conn.execute(sql, params)
        columns = [c[0] for c in cursor.description] if cursor.description else []
        rows = [list(r) for r in cursor.fetchmany(200)]  # cap result size
        return columns, rows
    finally:
        conn.close()


def get_rejected_records(db_path=DB_PATH):
    return run_query("SELECT transaction_id, reason FROM rejected_records", db_path=db_path)


if __name__ == "__main__":
    summary = load_csv_to_db()
    print(f"Loaded {summary['loaded']} rows")
    for r in summary["rejected"]:
        print(f"Rejected {r['transaction_id']}: {r['reason']}")
