"""Role definitions loaded from config/roles.yaml (the single source of truth for access)."""

from functools import cache
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict

from app.config import CONFIG_DIR
from app.db.models import Base

RoleName = Literal["inventory_manager", "procurement_manager", "owner"]

ALL_TABLES: tuple[str, ...] = tuple(sorted(Base.metadata.tables))


class RoleConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: RoleName
    label: str
    db_role: str
    description: str
    tables: dict[str, str]  # table -> why this role needs it
    excluded_columns: dict[str, frozenset[str]]
    starter_questions: tuple[str, ...] = ()
    actions: tuple[str, ...] = ()

    @property
    def allowed_tables(self) -> frozenset[str]:
        return frozenset(self.tables)


@cache
def load_roles() -> dict[str, RoleConfig]:
    raw = yaml.safe_load((CONFIG_DIR / "roles.yaml").read_text())["roles"]
    roles = {}
    for name, cfg in raw.items():
        tables = {t: "" for t in ALL_TABLES} if cfg["tables"] == "all" else cfg["tables"]
        unknown = set(tables) - set(ALL_TABLES)
        if unknown:
            raise ValueError(f"roles.yaml: unknown tables for {name}: {sorted(unknown)}")
        roles[name] = RoleConfig(
            name=name,
            label=cfg["label"],
            db_role=cfg["db_role"],
            description=" ".join(cfg["description"].split()),
            tables=tables,
            excluded_columns={t: frozenset(cols) for t, cols in (cfg.get("excluded_columns") or {}).items()},
            starter_questions=tuple(cfg.get("starter_questions", [])),
            actions=tuple(cfg.get("actions", [])),
        )
    return roles


def get_role(name: str) -> RoleConfig:
    try:
        return load_roles()[name]
    except KeyError:
        raise ValueError(f"Unknown role: {name}") from None
