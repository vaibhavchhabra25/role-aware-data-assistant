"""Read-only access to the company-provided source database.

The source DB is shared with other candidates, who have added their own
schemas and tables. We only look at the 17 tables that shipped with it.
"""

import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCS_DIR = REPO_ROOT / "docs"

SOURCE_TABLES = [
    "inv_categories",
    "inv_sub_categories",
    "inv_sub_categories_2",
    "inv_field_definitions",
    "inv_locations",
    "inv_issued_to_targets",
    "inv_items",
    "inv_current_stock",
    "inv_transactions",
    "proc_vendors",
    "proc_purchase_orders",
    "proc_po_lines",
    "proc_po_receipts",
    "proc_po_payment_tranches",
    "proc_po_line_regularisations",
    "proc_unexpected_receipt_headers",
    "proc_unexpected_receipts",
]


def connect() -> psycopg.Connection:
    """Connect with every transaction forced read-only at the session level."""
    load_dotenv(REPO_ROOT / ".env")
    return psycopg.connect(
        os.environ["SOURCE_DATABASE_URL"],
        connect_timeout=10,
        options="-c default_transaction_read_only=on -c statement_timeout=30000",
    )
