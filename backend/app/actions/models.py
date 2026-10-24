"""Structured write actions: what the model drafts, and the validated plan the user confirms.

The model never writes SQL for changes. It fills in an ActionDraft; code resolves names to
records, validates business rules and builds an ActionPlan. Only a confirmed plan is executed,
by fixed parameterised SQL in app/actions.
"""

import time
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field

ActionName = Literal["create_purchase_order", "adjust_stock"]


class DraftLine(BaseModel):
    item: str = Field(description="Item name or item code as the user wrote it.")
    quantity: float
    unit_price: float | None = Field(None, description="Only if the user stated a price.")


class ActionDraft(BaseModel):
    """The user's request as a structured action (filled in by the model)."""

    action: Literal["create_purchase_order", "adjust_stock", "unsupported"]
    # create_purchase_order
    supplier: str | None = None
    lines: list[DraftLine] = []
    expected_delivery_date: date | None = Field(None, description="Only if the user stated one.")
    # adjust_stock
    item: str | None = None
    warehouse: str | None = None
    quantity_change: float | None = Field(None, description="+ to add stock, - to remove.")
    counted_quantity: float | None = Field(None, description="If the user says what the count/actual quantity is.")
    reason: str | None = None
    # anything else
    missing: list[str] = Field(default_factory=list, description="Required details the user hasn't given.")
    explanation: str = Field("", description="For unsupported: what was asked, in one short sentence.")


class ActionError(Exception):
    """A draft that can't become a plan. `options` are clickable replies (e.g. which item)."""

    def __init__(self, message: str, options: list[str] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.options = options or []


class ActionPlan(BaseModel):
    """A validated action, shown to the user for confirmation and executed only if confirmed."""

    action: ActionName
    title: str
    summary: list[tuple[str, str]]  # label, value rows for the confirmation card
    table: dict[str, Any] | None = None  # {"columns": [...], "rows": [[...]]}
    payload: dict[str, Any]  # resolved ids and values used by execution
    created: float = Field(default_factory=time.time)

    def card(self) -> dict[str, Any]:
        return {"action": self.action, "title": self.title, "summary": [list(r) for r in self.summary], "table": self.table}
