"use client";

import { ArrowDown, ArrowUp } from "lucide-react";
import { useMemo, useState } from "react";

import { columnLabel, formatCell } from "@/lib/format";
import type { TableData } from "@/lib/types";
import { cn } from "@/lib/utils";

type Sort = { index: number; dir: "asc" | "desc" } | null;

export function DataTable({ table }: { table: TableData }) {
  const [sort, setSort] = useState<Sort>(null);
  const numeric = useMemo(
    () => table.columns.map((_, i) => table.rows.some((r) => typeof r[i] === "number")),
    [table],
  );

  const rows = useMemo(() => {
    if (!sort) return table.rows;
    const factor = sort.dir === "asc" ? 1 : -1;
    return [...table.rows].sort((a, b) => {
      const x = a[sort.index];
      const y = b[sort.index];
      if (x === y) return 0;
      if (x === null) return 1; // missing values last either way
      if (y === null) return -1;
      return (x < y ? -1 : 1) * factor;
    });
  }, [table.rows, sort]);

  const toggle = (index: number) =>
    setSort((s) => (s?.index !== index ? { index, dir: numeric[index] ? "desc" : "asc" } : { index, dir: s.dir === "asc" ? "desc" : "asc" }));

  return (
    <div className="overflow-hidden rounded-lg border">
      <div className="max-h-80 overflow-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 z-10 bg-muted/95 backdrop-blur">
            <tr>
              {table.columns.map((col, i) => (
                <th key={col} className={cn("px-3 py-2 font-medium text-muted-foreground", numeric[i] ? "text-right" : "text-left")}>
                  <button
                    type="button"
                    onClick={() => toggle(i)}
                    className={cn("inline-flex items-center gap-1 whitespace-nowrap hover:text-foreground", numeric[i] && "flex-row-reverse")}
                  >
                    {columnLabel(col)}
                    {sort?.index === i && (sort.dir === "asc" ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />)}
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r} className="border-t">
                {row.map((cell, i) => (
                  <td
                    key={i}
                    className={cn(
                      "px-3 py-1.5 align-top",
                      numeric[i] ? "text-right tabular-nums whitespace-nowrap" : "min-w-28",
                      cell === null && "text-muted-foreground",
                    )}
                  >
                    {formatCell(cell, table.columns[i])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="border-t bg-muted/40 px-3 py-1.5 text-xs text-muted-foreground">
        {table.rows.length.toLocaleString("en-IN")} {table.rows.length === 1 ? "row" : "rows"}
        {table.rows.length >= 500 && " (first 500 shown)"}
      </div>
    </div>
  );
}
