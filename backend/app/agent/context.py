"""Role-scoped context for the SQL generator: schema description and few-shot examples.

First security layer: the LLM only ever sees the tables, columns, joins and examples
the role is allowed to use. Retrieval is plain keyword overlap; with 17 tables and
~20 examples that is enough (no embeddings or vector store).
"""

import re
from dataclasses import dataclass
from functools import cache

import yaml

from app.config import AS_OF_DATE, CONFIG_DIR
from app.db.models import Base
from app.security.roles import RoleConfig
from app.security.sql_validator import referenced_tables

STOPWORDS = {
    "a", "an", "the", "of", "in", "on", "for", "to", "and", "or", "is", "are", "was", "were", "do", "does",
    "did", "we", "our", "us", "me", "my", "what", "which", "who", "how", "much", "many", "show", "list",
    "give", "tell", "with", "by", "from", "at", "it", "this", "that", "have", "has", "any", "all", "each",
}


@dataclass(frozen=True)
class Caveat:
    text: str  # contains {n}
    sql: str


@dataclass(frozen=True)
class Example:
    question: str
    sql: str
    format: str
    caveats: tuple[Caveat, ...]
    tables: frozenset[str]


def _render(text: str) -> str:
    return text.replace("{as_of}", AS_OF_DATE.isoformat())


@cache
def schema_docs() -> dict:
    return yaml.safe_load((CONFIG_DIR / "schema_docs.yaml").read_text())


@cache
def examples() -> tuple[Example, ...]:
    raw = yaml.safe_load((CONFIG_DIR / "examples.yaml").read_text())
    out = []
    for e in raw:
        sql = _render(e["sql"]).strip()
        caveats = tuple(Caveat(c["text"], _render(c["sql"]).strip()) for c in e.get("caveats", []))
        tables = referenced_tables(sql).union(*(referenced_tables(c.sql) for c in caveats))
        out.append(Example(e["question"], sql, e["format"], caveats, tables))
    return tuple(out)


def _tables_in(text: str) -> set[str]:
    return set(re.findall(r"\b((?:inv|proc)_[a-z0-9_]+)\.", text))


def _points_outside(column, allowed: frozenset[str]) -> bool:
    """True for a reference column to a table the role can't see (e.g. inv_transaction_id for Procurement)."""
    targets = {fk.column.table.name for fk in column.foreign_keys}
    if column.name.endswith("_id") and f"{column.name[:-3]}s" in Base.metadata.tables:
        targets.add(f"{column.name[:-3]}s")
    return bool(targets - allowed)


def build_schema_context(role: RoleConfig) -> str:
    """Markdown description of exactly what this role may query."""
    docs = schema_docs()
    allowed = role.allowed_tables
    skip = set(docs["skip_columns"])
    tier_order = {"core": 0, "secondary": 1, "rare": 2}

    lines = ["## Rules", *(f"- {_render(r)}" for r in docs["rules"]), "", "## Tables"]
    for name, table_doc in sorted(docs["tables"].items(), key=lambda kv: (tier_order[kv[1]["tier"]], kv[0])):
        if name not in allowed:
            continue
        hidden = role.excluded_columns.get(name, frozenset())
        lines += ["", f"### {name}", table_doc["description"]]
        described = table_doc.get("columns", {})
        for col in Base.metadata.tables[name].columns:
            if col.name in hidden or col.name in skip or _points_outside(col, allowed):
                continue
            note = f" — {described[col.name]}" if col.name in described else ""
            lines.append(f"- {col.name} ({col.type.compile().lower()}){note}")
        lines += [f"- Note: {n}" for n in table_doc.get("notes", [])]

    definitions = [d["text"] for d in docs.get("definitions", []) if set(d["tables"]) <= allowed]
    if definitions:
        lines += ["", "## Business definitions", *(f"- {d}" for d in definitions)]
    joins = [j for j in docs["joins"] if _tables_in(j) <= allowed]
    lines += ["", "## Joins", *(f"- {j}" for j in joins)]
    return "\n".join(lines)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if w not in STOPWORDS and len(w) > 1}


def select_examples(role: RoleConfig, question: str, k: int = 4) -> list[Example]:
    """The k examples most similar to the question, among those the role may see."""
    candidates = [e for e in examples() if e.tables <= role.allowed_tables]
    hidden = {c for cols in role.excluded_columns.values() for c in cols}
    candidates = [e for e in candidates if not any(re.search(rf"\b{c}\b", e.sql) for c in hidden)]
    q = _words(question)
    scored = sorted(candidates, key=lambda e: len(q & _words(e.question + " " + e.sql)), reverse=True)
    return scored[:k]
