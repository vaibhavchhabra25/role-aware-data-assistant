"use client";

import { LoaderCircle, LogOut } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { Answer } from "@/components/chat/answer";
import { Composer } from "@/components/chat/composer";
import { LoginForm } from "@/components/chat/login-form";
import { Button } from "@/components/ui/button";
import { AuthError, confirmAction, getHealth, getMe, getRoles, streamChat } from "@/lib/api";
import { formatAsOf } from "@/lib/format";
import { loadToken, saveToken } from "@/lib/session";
import type { ChatResponse, Message, RoleInfo, UserInfo } from "@/lib/types";

type Session = { token: string; user: UserInfo };

const newId = () => crypto.randomUUID();

/** Shows the sign-in form until there is a valid session, then the chat. */
export function ChatApp() {
  const [session, setSession] = useState<Session | null>(null);
  const [checking, setChecking] = useState(true);
  const [notice, setNotice] = useState<string>();

  // Resume a session from this tab if its token is still valid.
  useEffect(() => {
    const token = loadToken();
    const resume = token ? getMe(token).then((user) => ({ token, user })) : Promise.resolve(null);
    resume
      .catch(() => null)
      .then((s) => {
        if (!s) saveToken(null);
        setSession(s);
        setChecking(false);
      });
  }, []);

  const signOut = (message?: string) => {
    saveToken(null);
    setNotice(message);
    setSession(null);
  };

  if (checking) return null;
  if (!session) {
    return (
      <LoginForm
        notice={notice}
        onSignedIn={(res) => {
          saveToken(res.access_token);
          setNotice(undefined);
          setSession({ token: res.access_token, user: res.user });
        }}
      />
    );
  }
  return <Chat key={session.user.username} session={session} onSignOut={signOut} />;
}

function Chat({ session, onSignOut }: { session: Session; onSignOut: (message?: string) => void }) {
  const [roles, setRoles] = useState<RoleInfo[] | null>(null);
  const [asOf, setAsOf] = useState<string | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [messages, setMessages] = useState<Message[]>([]);
  const busy = messages.some((m) => m.from === "assistant" && m.status === "pending");
  const bottom = useRef<HTMLDivElement>(null);
  const abort = useRef<AbortController | null>(null);
  const threadId = useRef<string | null>(null); // created on the first question of a conversation

  const load = useCallback(
    () =>
      Promise.all([getRoles(), getHealth()])
        .then(([r, h]) => {
          setRoles(r);
          setAsOf(h.data_as_of);
          setLoadError(false);
        })
        .catch(() => setLoadError(true)),
    [],
  );

  useEffect(() => {
    load();
    return () => abort.current?.abort();
  }, [load]);
  useEffect(() => {
    bottom.current?.scrollIntoView({ behavior: "smooth", block: "end" }); // newer browsers return a Promise here
  }, [messages]);

  const ask = async (question: string) => {
    threadId.current ??= newId();
    const id = newId();
    setMessages((m) => [
      ...m,
      { id: newId(), from: "user", text: question },
      { id, from: "assistant", question, status: "pending", progress: "Understanding your question" },
    ]);
    const update = (patch: Partial<Extract<Message, { from: "assistant" }>>) =>
      setMessages((m) => m.map((msg) => (msg.id === id ? ({ ...msg, ...patch } as Message) : msg)));

    abort.current = new AbortController();
    try {
      const response = await streamChat(
        session.token,
        { thread_id: threadId.current, message: question },
        (p) => update({ progress: p.label }),
        abort.current.signal,
      );
      update({ status: "done", response });
    } catch (e) {
      if ((e as Error).name === "AbortError") return;
      if (e instanceof AuthError) return onSignOut(e.message);
      update({
        status: "done",
        response: {
          type: "error",
          answer: "I couldn't reach the assistant service. Check that it's running and try again.",
          table: null, chart: null, sql: null, caveats: [], trace_url: null,
        },
      });
    }
  };

  // Confirm or cancel the change shown on the latest confirmation card.
  const decide = async (decision: "confirm" | "cancel") => {
    if (!threadId.current) return;
    const id = newId();
    const label = decision === "confirm" ? "Confirm" : "Cancel";
    setMessages((m) => [
      ...m,
      { id: newId(), from: "user", text: label },
      { id, from: "assistant", question: label, status: "pending", progress: decision === "confirm" ? "Saving the change" : "Cancelling" },
    ]);
    const done = (response: ChatResponse) =>
      setMessages((m) => m.map((msg) => (msg.id === id ? { id, from: "assistant", question: label, status: "done", response } : msg)));
    try {
      done(await confirmAction(session.token, threadId.current, decision));
    } catch (e) {
      if (e instanceof AuthError) return onSignOut(e.message);
      done({ type: "error", answer: "I couldn't reach the assistant service, so nothing was changed.", table: null, chart: null, sql: null, caveats: [], trace_url: null });
    }
  };

  const role = roles?.find((r) => r.name === session.user.role);

  return (
    <div className="flex h-dvh flex-col">
      <header className="border-b bg-background/90 backdrop-blur">
        <div className="mx-auto flex max-w-3xl items-center gap-3 px-4 py-3">
          <div className="min-w-0 flex-1">
            <h1 className="text-sm font-semibold">Data Assistant</h1>
            <p className="truncate text-xs text-muted-foreground">{asOf ? `Data as of ${formatAsOf(asOf)}` : " "}</p>
          </div>
          <div className="text-right">
            <p className="text-sm">{session.user.display_name}</p>
            <p className="text-xs text-muted-foreground">@{session.user.username}</p>
          </div>
          <Button variant="ghost" size="icon" aria-label="Sign out" title="Sign out" onClick={() => onSignOut()}>
            <LogOut />
          </Button>
        </div>
      </header>

      <main className="flex-1 overflow-y-auto">
        <div className="mx-auto max-w-3xl px-4 py-6">
          {loadError ? (
            <div className="mt-16 text-center text-sm text-muted-foreground">
              <p>Can&apos;t reach the assistant service.</p>
              <button type="button" onClick={load} className="mt-2 text-foreground underline underline-offset-4">
                Try again
              </button>
            </div>
          ) : messages.length === 0 ? (
            role && <EmptyState role={role} onPick={ask} />
          ) : (
            <div className="space-y-6">
              {messages.map((m, i) =>
                m.from === "user" ? (
                  <div key={m.id} className="flex justify-end">
                    <p className="max-w-[85%] rounded-2xl rounded-br-md bg-secondary px-4 py-2.5 whitespace-pre-wrap">{m.text}</p>
                  </div>
                ) : m.status === "pending" ? (
                  <p key={m.id} className="flex items-center gap-2 text-sm text-muted-foreground" aria-live="polite">
                    <LoaderCircle className="size-4 animate-spin" /> {m.progress}…
                  </p>
                ) : (
                  <Answer
                    key={m.id}
                    response={m.response}
                    onRetry={() => ask(m.question)}
                    onAsk={ask}
                    onDecide={decide}
                    latest={i === messages.length - 1 && !busy}
                  />
                ),
              )}
            </div>
          )}
          <div ref={bottom} />
        </div>
      </main>

      <footer className="bg-background">
        <div className="mx-auto max-w-3xl px-4 pt-2 pb-4">
          <Composer disabled={busy || !roles} onSend={ask} />
          <p className="mt-2 text-center text-[11px] text-muted-foreground">
            Answers use only the data your role can see. Check important figures in the SQL.
          </p>
        </div>
      </footer>
    </div>
  );
}

function EmptyState({ role, onPick }: { role: RoleInfo; onPick: (q: string) => void }) {
  return (
    <div className="mt-[12vh]">
      <h2 className="text-xl font-medium tracking-tight">What would you like to know?</h2>
      <p className="mt-1.5 max-w-xl text-sm text-muted-foreground">
        As <span className="text-foreground">{role.label}</span> you can ask about: {role.description}
      </p>
      <div className="mt-6 grid gap-2 sm:grid-cols-2">
        {role.starter_questions.map((q) => (
          <button
            key={q}
            type="button"
            onClick={() => onPick(q)}
            className="rounded-xl border bg-card px-3.5 py-3 text-left text-sm transition-colors hover:bg-accent hover:text-accent-foreground"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}
