"use client";

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { columnLabel, formatAxis, formatCell } from "@/lib/format";
import type { ChartSpec } from "@/lib/types";

const COLORS = ["var(--chart-1)", "var(--chart-2)", "var(--chart-3)", "var(--chart-4)", "var(--chart-5)"];
const AXIS = { stroke: "var(--muted-foreground)", fontSize: 12, tickLine: false, axisLine: false } as const;
const TOOLTIP_STYLE = {
  background: "var(--popover)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  fontSize: 12,
  color: "var(--popover-foreground)",
};

export function ResultChart({ chart }: { chart: ChartSpec }) {
  const valueFormatter = (value: unknown, name: unknown) =>
    [formatCell(value as number, String(name)), columnLabel(String(name))] as [string, string];

  return (
    <figure className="rounded-lg border p-3">
      {chart.title && <figcaption className="mb-2 text-sm font-medium">{chart.title}</figcaption>}
      {chart.kind === "pie" ? <PieView chart={chart} /> : chart.kind === "line" ? <LineView chart={chart} fmt={valueFormatter} /> : <BarView chart={chart} fmt={valueFormatter} />}
    </figure>
  );
}

type ViewProps = { chart: ChartSpec; fmt: (v: unknown, n: unknown) => [string, string] };

function LineView({ chart, fmt }: ViewProps) {
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={chart.data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid vertical={false} stroke="var(--border)" />
        <XAxis dataKey={chart.x} {...AXIS} tickMargin={8} />
        <YAxis {...AXIS} width={56} tickFormatter={(v) => formatAxis(v, chart.y[0])} />
        <Tooltip contentStyle={TOOLTIP_STYLE} formatter={fmt} />
        {chart.y.length > 1 && <Legend formatter={(v) => columnLabel(String(v))} wrapperStyle={{ fontSize: 12 }} />}
        {chart.y.map((y, i) => (
          <Line key={y} dataKey={y} type="monotone" stroke={COLORS[i % COLORS.length]} strokeWidth={2} dot={{ r: 3 }} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

function BarView({ chart, fmt }: ViewProps) {
  // Many or long category names read better as horizontal bars.
  const horizontal = chart.data.length > 6 || chart.data.some((d) => String(d[chart.x] ?? "").length > 14);
  const height = horizontal ? Math.max(200, chart.data.length * 28 + 40) : 260;
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart data={chart.data} layout={horizontal ? "vertical" : "horizontal"} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
        <CartesianGrid horizontal={!horizontal} vertical={horizontal} stroke="var(--border)" />
        {horizontal ? (
          <>
            <XAxis type="number" {...AXIS} tickFormatter={(v) => formatAxis(v, chart.y[0])} />
            <YAxis type="category" dataKey={chart.x} {...AXIS} width={150} tickFormatter={(v) => truncate(String(v), 22)} />
          </>
        ) : (
          <>
            <XAxis dataKey={chart.x} {...AXIS} tickMargin={8} />
            <YAxis {...AXIS} width={56} tickFormatter={(v) => formatAxis(v, chart.y[0])} />
          </>
        )}
        <Tooltip contentStyle={TOOLTIP_STYLE} formatter={fmt} cursor={{ fill: "var(--muted)" }} />
        {chart.y.length > 1 && <Legend formatter={(v) => columnLabel(String(v))} wrapperStyle={{ fontSize: 12 }} />}
        {chart.y.map((y, i) => (
          <Bar key={y} dataKey={y} fill={COLORS[i % COLORS.length]} radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]} maxBarSize={36} />
        ))}
      </BarChart>
    </ResponsiveContainer>
  );
}

function PieView({ chart }: { chart: ChartSpec }) {
  const y = chart.y[0];
  return (
    <ResponsiveContainer width="100%" height={260}>
      <PieChart>
        <Pie data={chart.data} dataKey={y} nameKey={chart.x} innerRadius={55} outerRadius={95} paddingAngle={2} stroke="var(--card)">
          {chart.data.map((_, i) => (
            <Cell key={i} fill={COLORS[i % COLORS.length]} />
          ))}
        </Pie>
        <Tooltip contentStyle={TOOLTIP_STYLE} formatter={(v) => formatCell(v as number, y)} />
        <Legend wrapperStyle={{ fontSize: 12 }} />
      </PieChart>
    </ResponsiveContainer>
  );
}

const truncate = (s: string, n: number) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
