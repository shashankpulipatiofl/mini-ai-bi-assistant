# Sales Data Dictionary

This document describes the sales dataset used by the Mini AI BI Assistant.
It is the knowledge source for questions ABOUT the dataset (columns, meanings,
business rules), answered with retrieval-augmented generation (RAG).

## Dataset overview

The dataset contains sales transactions (orders) of office products from
January 2026 to June 2026. The source CSV file has 51 rows. Each row is one
transaction. After cleaning, 49 rows are stored in the `sales` table of a SQLite
database, and 2 invalid rows are stored in the `rejected_records` table.

## Columns and attributes

The dataset has 10 original columns (attributes / fields) plus 2 derived columns:

- transaction_id: unique ID of the transaction, for example TXN0001.
- order_date: the date the order was placed. In the CSV it is DD-MM-YYYY; in the database it is stored as YYYY-MM-DD.
- product: the product that was sold.
- category: the product category (Accessories, Furniture, Electronics).
- region: the sales region (North, South, East, West).
- sales_channel: how the product was sold, Online or Retail (in a physical store).
- quantity: number of units sold in the transaction.
- unit_price: price of one unit.
- discount_amount: total discount given on the transaction (an amount, not a percentage).
- status: Completed or Returned.
- order_month (derived): the month of the order as YYYY-MM, used for monthly analysis.
- revenue (derived): quantity x unit_price - discount_amount.

## Products and categories

There are 7 products in 3 categories:

- Accessories: Laptop Stand, Wireless Mouse, Keyboard, USB-C Hub.
- Furniture: Office Chair, Desk.
- Electronics: Monitor.

Each product always has the same unit price in this data: Laptop Stand 1200,
Wireless Mouse 650, Keyboard 900, USB-C Hub 1800, Office Chair 7800, Desk 12500,
Monitor 9500.

## Regions and sales channels

Regions: North, South, East and West.
Sales channels: Online (e-commerce orders) and Retail (store sales).

## Revenue definition and business rules

Revenue of a transaction = quantity x unit_price - discount_amount.
Only transactions with status = Completed count as revenue. Returned orders are
kept in the data as valid records but are excluded from all revenue and sales
figures. "Sales" in questions means revenue.

## Order status

Completed means the order was delivered and paid. Returned means the customer
sent the product back, so it does not count as revenue. In the cleaned data
there are 48 Completed orders and 1 Returned order (TXN0033).

## Data quality and cleaning

Rows are validated when the CSV is loaded. A row is rejected if a required field
is missing, the date is invalid, quantity is not greater than 0, unit price is
missing or not greater than 0, the discount is negative or larger than the order
value, the status is unknown, or the transaction ID is duplicated.
Two rows were rejected: TXN0034 (quantity is 0) and TXN0051 (unit price is
missing). Rejected rows are not guessed or filled in.

## What the data does not contain

The dataset has no cost, profit, margin, customer, salesperson, inventory or
stock information, and no data after June 2026. Questions about these cannot be
answered from this data.
