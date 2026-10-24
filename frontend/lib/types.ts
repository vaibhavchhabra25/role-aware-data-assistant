// Mirrors backend/app/schemas.py.

export type RoleName = "inventory_manager" | "procurement_manager" | "owner";

export type RoleInfo = {
  name: RoleName;
  label: string;
  description: string;
  starter_questions: string[];
};

export type Health = {
  status: "ok" | "degraded";
  database: boolean;
  data_as_of: string;
};

export type Cell = string | number | boolean | null;

export type TableData = {
  columns: string[];
  rows: Cell[][];
};

export type ChartSpec = {
  kind: "bar" | "line" | "pie";
  x: string;
  y: string[];
  title: string;
  data: Record<string, Cell>[];
};

export type ChatResponse = {
  type: "text" | "table" | "chart" | "refusal" | "clarification" | "confirmation" | "error";
  answer: string;
  table: TableData | null;
  chart: ChartSpec | null;
  sql: string | null;
  caveats: string[];
  options?: string[]; // clickable replies to a clarifying question
  action?: ActionCard | null; // a drafted change waiting for confirmation
  trace_url: string | null;
};

export type ActionCard = {
  action: "create_purchase_order" | "adjust_stock";
  title: string;
  summary: [string, string][];
  table: TableData | null;
};

export type Progress = { step: string; label: string };

export type UserInfo = { username: string; display_name: string; role: RoleName };

export type LoginResponse = { access_token: string; token_type: "bearer"; user: UserInfo };

export type Message =
  | { id: string; from: "user"; text: string }
  | { id: string; from: "assistant"; question: string; status: "pending"; progress: string }
  | { id: string; from: "assistant"; question: string; status: "done"; response: ChatResponse };
