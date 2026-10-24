import type { ChatResponse, Health, LoginResponse, Progress, RoleInfo, UserInfo } from "@/lib/types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Thrown when the token is missing, expired or rejected: the UI goes back to sign-in. */
export class AuthError extends Error {}

async function request<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`, {
    ...init,
    cache: "no-store",
    headers: {
      ...(init.body ? { "Content-Type": "application/json" } : {}),
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
  });
  if (res.status === 401) throw new AuthError((await res.json().catch(() => null))?.detail ?? "Please sign in.");
  if (!res.ok) throw new Error(`${path} failed: ${res.status}`);
  return res.json();
}

export const getRoles = () => request<RoleInfo[]>("/api/roles");
export const getHealth = () => request<Health>("/api/health");
export const getMe = (token: string) => request<UserInfo>("/api/auth/me", {}, token);
export const login = (username: string, password: string) =>
  request<LoginResponse>("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });

/** Confirm or cancel the change waiting in this conversation. */
export async function confirmAction(token: string, threadId: string, decision: "confirm" | "cancel"): Promise<ChatResponse> {
  const res = await fetch(`${API_URL}/api/chat/confirm`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ thread_id: threadId, decision }),
  });
  if (res.status === 401) throw new AuthError("Your session has ended. Please sign in again.");
  if (res.status === 409) {
    return { type: "text", answer: "That change is no longer waiting for confirmation.", table: null, chart: null, sql: null, caveats: [], trace_url: null };
  }
  if (!res.ok) throw new Error(`confirm failed: ${res.status}`);
  return res.json();
}

/**
 * POST /api/chat/stream and read its Server-Sent Events: `progress` events while the
 * agent works, then one `result` event. The role comes from the token on the server.
 */
export async function streamChat(
  token: string,
  request: { thread_id: string; message: string },
  onProgress: (progress: Progress) => void,
  signal?: AbortSignal,
): Promise<ChatResponse> {
  const res = await fetch(`${API_URL}/api/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify(request),
    signal,
  });
  if (res.status === 401) throw new AuthError("Your session has ended. Please sign in again.");
  if (!res.ok || !res.body) throw new Error(`chat failed: ${res.status}`);

  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += value;
    let boundary: number;
    while ((boundary = buffer.indexOf("\n\n")) !== -1) {
      const block = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const event = /^event: (.+)$/m.exec(block)?.[1];
      const data = /^data: (.+)$/m.exec(block)?.[1];
      if (!event || !data) continue;
      if (event === "progress") onProgress(JSON.parse(data));
      if (event === "result") return JSON.parse(data);
    }
  }
  throw new Error("The answer stream ended without a result.");
}
