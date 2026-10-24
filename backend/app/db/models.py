"""SQLAlchemy declarations for the 17 company tables.

Mirrors the database exactly (types, nullability, defaults, keys, constraints,
indexes). Relationships the database does not enforce with a foreign key are
declared as view-only relationships with `foreign()` so they document the link
without creating a constraint; they are marked "not enforced" below.

Conventions in the data:
- Every `id` is a UUID stored as varchar.
- `created_by` / `received_by` / ... are user ids with no users table; never show them.
- Timestamps are naive (no time zone).
- `is_synthetic` marks generated rows; treat them as normal data.
"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Double,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

QTY = Numeric(14, 3)
MONEY = Numeric(14, 2)
PERCENT = Numeric(5, 2)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------
# Inventory: item master and classification
# --------------------------------------------------------------------------


class InvCategory(Base):
    """Top-level item category: Raw Material, Bought Out, Finished Goods."""

    __tablename__ = "inv_categories"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    short_code: Mapped[str] = mapped_column(String, unique=True)  # RM, BO, FG; prefix of item_code
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    sub_categories: Mapped[list["InvSubCategory"]] = relationship(back_populates="category")


class InvSubCategory(Base):
    """Second level, e.g. Metal, Non-Metal, Mechanical."""

    __tablename__ = "inv_sub_categories"
    __table_args__ = (
        UniqueConstraint("category_id", "name"),
        UniqueConstraint("category_id", "short_code"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    short_code: Mapped[str] = mapped_column(String)
    category_id: Mapped[str] = mapped_column(ForeignKey("inv_categories.id", ondelete="RESTRICT"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime)

    category: Mapped[InvCategory] = relationship(back_populates="sub_categories")
    families: Mapped[list["InvSubCategory2"]] = relationship(back_populates="sub_category")


class InvSubCategory2(Base):
    """Third level item family (e.g. Flat Sheet, Hexalobular Screw) with naming standards."""

    __tablename__ = "inv_sub_categories_2"
    __table_args__ = (
        UniqueConstraint("sub_category_id", "name"),
        UniqueConstraint("sub_category_id", "short_code"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    short_code: Mapped[str] = mapped_column(String)
    sub_category_id: Mapped[str] = mapped_column(ForeignKey("inv_sub_categories.id", ondelete="RESTRICT"), index=True)
    source_id: Mapped[str | None] = mapped_column(String)  # legacy code, e.g. SC-001
    standard: Mapped[str | None] = mapped_column(Text)  # e.g. IS 2062
    description: Mapped[str | None] = mapped_column(Text)
    naming_convention: Mapped[str | None] = mapped_column(Text)
    convention_notes: Mapped[str | None] = mapped_column(Text)  # JSON list as text
    examples: Mapped[str | None] = mapped_column(Text)  # JSON list as text
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    hsn_code: Mapped[str | None] = mapped_column(String)  # Indian tariff code
    nomenclature_fields: Mapped[str | None] = mapped_column(Text)  # JSON object as text
    nomenclature_version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    cut_shape: Mapped[str | None] = mapped_column(String)  # unused (always NULL)
    cut_formula: Mapped[str | None] = mapped_column(Text)  # unused (always NULL)

    sub_category: Mapped[InvSubCategory] = relationship(back_populates="families")


class InvFieldDefinition(Base):
    """Custom item fields (alias, source, legacy_product_code). Configuration, not data."""

    __tablename__ = "inv_field_definitions"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    field_type: Mapped[str] = mapped_column(String)
    required: Mapped[bool] = mapped_column(Boolean)
    options: Mapped[str | None] = mapped_column(Text)  # JSON list as text
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)


class InvItem(Base):
    """Item master. Shared by inventory and procurement."""

    __tablename__ = "inv_items"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    item_code: Mapped[str] = mapped_column(String, unique=True, index=True)  # e.g. BO-MC-0081
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    category_id: Mapped[str | None] = mapped_column(ForeignKey("inv_categories.id", ondelete="SET NULL"), index=True)
    sub_category_id: Mapped[str | None] = mapped_column(ForeignKey("inv_sub_categories.id", ondelete="SET NULL"), index=True)
    sub_category_2_id: Mapped[str | None] = mapped_column(
        ForeignKey("inv_sub_categories_2.id", ondelete="SET NULL"), index=True
    )
    status: Mapped[str] = mapped_column(String)  # active | inactive
    unit: Mapped[str] = mapped_column(String)  # pcs, kg, set, roll, pair, mtr
    hsn_code: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(Text)
    specifications: Mapped[str | None] = mapped_column(Text)  # always '{}'
    alt_units: Mapped[str | None] = mapped_column(Text)  # always '[]'
    is_spare: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), index=True)

    category: Mapped[InvCategory | None] = relationship()
    sub_category: Mapped[InvSubCategory | None] = relationship()
    family: Mapped[InvSubCategory2 | None] = relationship()


# --------------------------------------------------------------------------
# Inventory: locations, stock and movements
# --------------------------------------------------------------------------


class InvLocation(Base):
    """Warehouse / store location."""

    __tablename__ = "inv_locations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)  # e.g. 132-1
    address: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class InvIssuedToTarget(Base):
    """Department or team that stock is issued to (shop floor, dispatch, ...)."""

    __tablename__ = "inv_issued_to_targets"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime)


class InvCurrentStock(Base):
    """On-hand quantity per item per location, with reorder (min) and max levels."""

    __tablename__ = "inv_current_stock"
    __table_args__ = (
        UniqueConstraint("item_id", "location_id"),
        CheckConstraint("quantity >= 0"),
        CheckConstraint("min_stock_level >= 0 AND max_stock_level >= min_stock_level"),
    )

    id: Mapped[str] = mapped_column(String, primary_key=True)
    item_id: Mapped[str] = mapped_column(String, index=True)  # -> inv_items.id (not enforced)
    location_id: Mapped[str] = mapped_column(ForeignKey("inv_locations.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[float] = mapped_column(Double)  # in the item's unit
    min_stock_level: Mapped[float] = mapped_column(Double, server_default=text("0.000"))  # reorder level; 0 = not set
    max_stock_level: Mapped[float] = mapped_column(Double, server_default=text("0.000"))
    last_transaction_at: Mapped[datetime | None] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    item: Mapped[InvItem] = relationship(primaryjoin="foreign(InvCurrentStock.item_id) == InvItem.id", viewonly=True)
    location: Mapped[InvLocation] = relationship()


class InvTransaction(Base):
    """Stock movement ledger. Sum of quantity per (item, location) = current stock.

    quantity is signed: positive for inbound / transfer_in / positive adjustments,
    negative for outbound / transfer_out / negative adjustments.
    """

    __tablename__ = "inv_transactions"
    __table_args__ = (Index("ix_inv_transactions_item_location", "item_id", "location_id"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    item_id: Mapped[str] = mapped_column(String, index=True)  # -> inv_items.id (not enforced)
    location_id: Mapped[str | None] = mapped_column(ForeignKey("inv_locations.id", ondelete="RESTRICT"), index=True)
    # inbound | outbound | adjustment | transfer_in | transfer_out | receipt
    transaction_type: Mapped[str] = mapped_column(String)
    quantity: Mapped[float] = mapped_column(Double)
    # warehouse_issue | reconciliation | unexpected_receipt | purchase_order | transfer
    reference_type: Mapped[str | None] = mapped_column(String)
    reference_id: Mapped[str | None] = mapped_column(String, index=True)  # id in the table named by reference_type
    transfer_location_id: Mapped[str | None] = mapped_column(String)  # -> inv_locations.id (not enforced)
    unit_cost: Mapped[float | None] = mapped_column(Double)  # hidden from Inventory Manager
    notes: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    currency: Mapped[str] = mapped_column(String(3), server_default="INR")
    issued_to_id: Mapped[str | None] = mapped_column(String, index=True)  # -> inv_issued_to_targets.id (not enforced)
    is_synthetic: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))

    item: Mapped[InvItem] = relationship(primaryjoin="foreign(InvTransaction.item_id) == InvItem.id", viewonly=True)
    location: Mapped[InvLocation | None] = relationship(foreign_keys=[location_id])
    transfer_location: Mapped[InvLocation | None] = relationship(
        primaryjoin="foreign(InvTransaction.transfer_location_id) == InvLocation.id", viewonly=True
    )
    issued_to: Mapped[InvIssuedToTarget | None] = relationship(
        primaryjoin="foreign(InvTransaction.issued_to_id) == InvIssuedToTarget.id", viewonly=True
    )


# --------------------------------------------------------------------------
# Procurement: suppliers and purchase orders
# --------------------------------------------------------------------------


class ProcVendor(Base):
    """Supplier master."""

    __tablename__ = "proc_vendors"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, unique=True, index=True)
    code: Mapped[str | None] = mapped_column(String, unique=True)  # always NULL
    city: Mapped[str | None] = mapped_column(String)
    payment_terms: Mapped[str | None] = mapped_column(String)  # free text, e.g. "30 Days Credit"
    is_active: Mapped[bool] = mapped_column(Boolean)
    created_by: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)

    purchase_orders: Mapped[list["ProcPurchaseOrder"]] = relationship(back_populates="vendor")


class ProcPurchaseOrder(Base):
    """Purchase order header. Amounts in INR; totals equal the sum of line totals."""

    __tablename__ = "proc_purchase_orders"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    po_number: Mapped[str] = mapped_column(String, unique=True, index=True)  # e.g. PO-2026-0003
    vendor_id: Mapped[str] = mapped_column(ForeignKey("proc_vendors.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String, index=True)  # placed | partial | received
    placed_on: Mapped[date | None] = mapped_column(Date, index=True)
    currency: Mapped[str] = mapped_column(String)
    subtotal: Mapped[Decimal] = mapped_column(MONEY)
    tax_amount: Mapped[Decimal] = mapped_column(MONEY)
    total_amount: Mapped[Decimal] = mapped_column(MONEY)
    payment_terms: Mapped[str | None] = mapped_column(String)
    delivery_location_id: Mapped[str | None] = mapped_column(String)  # -> inv_locations.id (not enforced)
    ship_to_address: Mapped[str | None] = mapped_column(Text)  # always NULL
    custom_fields: Mapped[str | None] = mapped_column(Text)  # always '{}'
    created_by: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    # JSON list of ISO dates, one per revision: first = original promise, last = current expectation
    expected_delivery_date: Mapped[list[str] | None] = mapped_column(JSON)
    expected_dispatch_date: Mapped[list[str] | None] = mapped_column(JSON)

    vendor: Mapped[ProcVendor] = relationship(back_populates="purchase_orders")
    delivery_location: Mapped[InvLocation | None] = relationship(
        primaryjoin="foreign(ProcPurchaseOrder.delivery_location_id) == InvLocation.id", viewonly=True
    )
    lines: Mapped[list["ProcPoLine"]] = relationship(back_populates="purchase_order")
    payment_tranches: Mapped[list["ProcPoPaymentTranche"]] = relationship(
        primaryjoin="ProcPurchaseOrder.id == foreign(ProcPoPaymentTranche.po_id)", viewonly=True
    )


class ProcPoLine(Base):
    """One item on a PO: quantity ordered vs received, price and tax."""

    __tablename__ = "proc_po_lines"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    po_id: Mapped[str] = mapped_column(ForeignKey("proc_purchase_orders.id", ondelete="CASCADE"), index=True)
    inv_item_id: Mapped[str | None] = mapped_column(String, index=True)  # -> inv_items.id (not enforced)
    quantity_ordered: Mapped[Decimal] = mapped_column(QTY)
    quantity_received: Mapped[Decimal] = mapped_column(QTY)  # can exceed ordered (over-receipt)
    unit: Mapped[str | None] = mapped_column(String)  # inconsistent casing: pcs, Nos., KG, ...
    unit_price: Mapped[Decimal] = mapped_column(MONEY)
    tax_percentage: Mapped[Decimal] = mapped_column(PERCENT)
    line_subtotal: Mapped[Decimal] = mapped_column(MONEY)
    line_tax: Mapped[Decimal] = mapped_column(MONEY)
    line_total: Mapped[Decimal] = mapped_column(MONEY)
    notes: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer)
    custom_fields: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)
    is_regularised: Mapped[bool] = mapped_column(Boolean, server_default=text("false"), index=True)
    expected_delivery_date: Mapped[list[str] | None] = mapped_column(JSON)  # same format as the PO header
    expected_dispatch_date: Mapped[list[str] | None] = mapped_column(JSON)

    purchase_order: Mapped[ProcPurchaseOrder] = relationship(back_populates="lines")
    item: Mapped[InvItem | None] = relationship(primaryjoin="foreign(ProcPoLine.inv_item_id) == InvItem.id", viewonly=True)
    receipts: Mapped[list["ProcPoReceipt"]] = relationship(back_populates="po_line")


class ProcPoReceipt(Base):
    """A delivery received against a PO line. Each one also creates an inbound stock movement."""

    __tablename__ = "proc_po_receipts"
    __table_args__ = (Index("ix_proc_po_receipts_inv_txn_id", "inv_transaction_id"),)  # duplicate index in the DB

    id: Mapped[str] = mapped_column(String, primary_key=True)
    po_line_id: Mapped[str] = mapped_column(ForeignKey("proc_po_lines.id", ondelete="CASCADE"), index=True)
    quantity: Mapped[Decimal] = mapped_column(QTY)
    location_id: Mapped[str | None] = mapped_column(String)  # -> inv_locations.id (not enforced)
    notes: Mapped[str | None] = mapped_column(Text)  # usually the supplier invoice number
    inv_transaction_id: Mapped[str | None] = mapped_column(
        ForeignKey("inv_transactions.id", ondelete="SET NULL"), index=True
    )
    received_by: Mapped[str] = mapped_column(String)
    received_at: Mapped[datetime] = mapped_column(DateTime, index=True)

    po_line: Mapped[ProcPoLine] = relationship(back_populates="receipts")
    location: Mapped[InvLocation | None] = relationship(
        primaryjoin="foreign(ProcPoReceipt.location_id) == InvLocation.id", viewonly=True
    )
    inv_transaction: Mapped[InvTransaction | None] = relationship()


class ProcPoPaymentTranche(Base):
    """Payment schedule of a PO, split into stages (advance, against dispatch, credit)."""

    __tablename__ = "proc_po_payment_tranches"
    __table_args__ = (Index("ix_proc_po_payment_tranches_po_id_sequence", "po_id", "sequence"),)

    id: Mapped[str] = mapped_column(String, primary_key=True)
    po_id: Mapped[str] = mapped_column(String, index=True)  # -> proc_purchase_orders.id (not enforced)
    sequence: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String)  # free text, e.g. "30 Days Credit"
    percentage: Mapped[Decimal] = mapped_column(PERCENT)  # tranches of a PO add up to 100
    amount: Mapped[Decimal | None] = mapped_column(MONEY)  # always NULL; derive as percentage x PO total
    is_advance: Mapped[bool] = mapped_column(Boolean, index=True)
    blocks_dispatch: Mapped[bool] = mapped_column(Boolean, index=True)
    due_basis: Mapped[str | None] = mapped_column(String)  # advance | dispatch | credit
    due_offset_days: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String)  # always 'extraction'
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime, index=True)  # always NULL: no payments recorded
    cleared_by: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    updated_at: Mapped[datetime] = mapped_column(DateTime)

    purchase_order: Mapped[ProcPurchaseOrder] = relationship(
        primaryjoin="foreign(ProcPoPaymentTranche.po_id) == ProcPurchaseOrder.id", viewonly=True
    )


class ProcPoLineRegularisation(Base):
    """Correction when a PO line was received in a different quantity than ordered."""

    __tablename__ = "proc_po_line_regularisations"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    po_id: Mapped[str] = mapped_column(ForeignKey("proc_purchase_orders.id", ondelete="CASCADE"), index=True)
    po_line_id: Mapped[str] = mapped_column(ForeignKey("proc_po_lines.id", ondelete="CASCADE"), index=True)
    ordered_qty: Mapped[Decimal] = mapped_column(QTY)
    received_qty_before: Mapped[Decimal] = mapped_column(QTY)
    regularised_qty: Mapped[Decimal] = mapped_column(QTY)
    received_qty_after: Mapped[Decimal] = mapped_column(QTY)
    qty_variance: Mapped[Decimal] = mapped_column(QTY)  # received_after - ordered
    unit_price: Mapped[Decimal] = mapped_column(MONEY)
    actual_amount: Mapped[Decimal] = mapped_column(MONEY)
    expected_amount: Mapped[Decimal] = mapped_column(MONEY)
    amount_variance: Mapped[Decimal] = mapped_column(MONEY)
    notes: Mapped[str | None] = mapped_column(Text)
    inv_transaction_id: Mapped[str | None] = mapped_column(String, index=True)  # -> inv_transactions.id (not enforced)
    regularised_by: Mapped[str] = mapped_column(String)
    regularised_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    review_notes: Mapped[str | None] = mapped_column(Text)

    purchase_order: Mapped[ProcPurchaseOrder] = relationship()
    po_line: Mapped[ProcPoLine] = relationship()


# --------------------------------------------------------------------------
# Procurement: goods received without a PO
# --------------------------------------------------------------------------


class ProcUnexpectedReceiptHeader(Base):
    """A delivery that arrived without a matching PO (one supplier invoice)."""

    __tablename__ = "proc_unexpected_receipt_headers"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    received_by: Mapped[str] = mapped_column(String)
    received_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    notes: Mapped[str | None] = mapped_column(Text)  # usually the supplier invoice number
    vendor_name: Mapped[str | None] = mapped_column(String)  # free text, not linked to proc_vendors

    receipts: Mapped[list["ProcUnexpectedReceipt"]] = relationship(back_populates="header")


class ProcUnexpectedReceipt(Base):
    """One item in an unexpected delivery. Resolved once linked to a PO (none are yet)."""

    __tablename__ = "proc_unexpected_receipts"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    inv_item_id: Mapped[str] = mapped_column(String, index=True)  # -> inv_items.id (not enforced)
    quantity: Mapped[Decimal] = mapped_column(QTY)
    location_id: Mapped[str | None] = mapped_column(String)  # -> inv_locations.id (not enforced)
    notes: Mapped[str | None] = mapped_column(Text)
    inv_transaction_id: Mapped[str | None] = mapped_column(String, index=True)  # -> inv_transactions.id (not enforced)
    received_by: Mapped[str] = mapped_column(String)
    received_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    resolved_po_id: Mapped[str | None] = mapped_column(
        ForeignKey("proc_purchase_orders.id", ondelete="SET NULL"), index=True
    )
    resolved_by: Mapped[str | None] = mapped_column(String)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)
    header_id: Mapped[str | None] = mapped_column(
        Text, ForeignKey("proc_unexpected_receipt_headers.id", ondelete="CASCADE"), index=True
    )

    header: Mapped[ProcUnexpectedReceiptHeader | None] = relationship(back_populates="receipts")
    item: Mapped[InvItem] = relationship(
        primaryjoin="foreign(ProcUnexpectedReceipt.inv_item_id) == InvItem.id", viewonly=True
    )
    location: Mapped[InvLocation | None] = relationship(
        primaryjoin="foreign(ProcUnexpectedReceipt.location_id) == InvLocation.id", viewonly=True
    )
    resolved_po: Mapped[ProcPurchaseOrder | None] = relationship()
