"""Write actions. Each has: plan() to resolve + validate a draft, execute() with fixed SQL,
a dedicated writer login with only the grants it needs, and a confirmation message."""

from dataclasses import dataclass
from types import ModuleType

from app.actions import purchase_order, stock


@dataclass(frozen=True)
class Action:
    name: str
    label: str  # "create purchase orders": what a role can do
    task: str  # "create a purchase order": one instance
    module: ModuleType

    @property
    def writer(self) -> str:
        return self.module.WRITER


ACTIONS = {
    "create_purchase_order": Action("create_purchase_order", "create purchase orders", "create a purchase order", purchase_order),
    "adjust_stock": Action("adjust_stock", "adjust stock", "adjust stock", stock),
}
