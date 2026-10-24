import type { Cell } from "@/lib/types";

// Keep in sync with backend/app/agent/nodes/respond.py (is_money).
const MONEY_WORDS = ["amount", "spend", "value", "price", "cost", "inr", "line_total"];
const NOT_MONEY_WORDS = ["pct", "percent", "share", "ratio", "rate", "count", "qty", "quantity", "days"];
const indian = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 });
const compactIndian = new Intl.NumberFormat("en-IN", { notation: "compact", maximumFractionDigits: 1 });

export function isMoneyColumn(column: string): boolean {
  const name = column.toLowerCase();
  return MONEY_WORDS.some((w) => name.includes(w)) && !NOT_MONEY_WORDS.some((w) => name.includes(w));
}

/** snake_case column name -> "Sentence case" header. */
export function columnLabel(column: string): string {
  const text = column.replace(/_inr$/, "").replace(/_/g, " ").trim();
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** A table cell: Indian digit grouping, ₹ for money columns, dash for missing. */
export function formatCell(value: Cell, column: string): string {
  if (value === null || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return (isMoneyColumn(column) ? "₹" : "") + indian.format(value);
  return value;
}

/** Short axis labels for charts: 12,34,567 -> 12L. */
export function formatAxis(value: number, column: string): string {
  return (isMoneyColumn(column) ? "₹" : "") + compactIndian.format(value);
}

export function formatAsOf(isoDate: string): string {
  return new Date(`${isoDate}T00:00:00`).toLocaleDateString("en-IN", { day: "numeric", month: "short", year: "numeric" });
}
