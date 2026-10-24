"use client";

import { LoaderCircle } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { AuthError, login } from "@/lib/api";
import type { LoginResponse } from "@/lib/types";

const DEMO_USERS = ["inventory", "procurement", "owner"];

export function LoginForm({ onSignedIn, notice }: { onSignedIn: (res: LoginResponse) => void; notice?: string }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(notice ?? null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      onSignedIn(await login(username, password));
    } catch (err) {
      setError(err instanceof AuthError ? err.message : "Can't reach the assistant service. Try again in a moment.");
      setBusy(false);
    }
  };

  return (
    <div className="flex min-h-dvh items-center justify-center px-4">
      <form onSubmit={submit} className="w-full max-w-sm space-y-5">
        <div>
          <h1 className="text-xl font-medium tracking-tight">Data Assistant</h1>
          <p className="mt-1 text-sm text-muted-foreground">Sign in to ask about stock, orders and suppliers.</p>
        </div>

        <div className="space-y-3">
          <label className="block space-y-1.5">
            <span className="text-sm">Username</span>
            <input
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoCapitalize="none"
              required
              className="h-10 w-full rounded-lg border bg-card px-3 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/30"
            />
          </label>
          <label className="block space-y-1.5">
            <span className="text-sm">Password</span>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              required
              className="h-10 w-full rounded-lg border bg-card px-3 text-sm outline-none focus-visible:ring-3 focus-visible:ring-ring/30"
            />
          </label>
        </div>

        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}

        <Button type="submit" size="lg" className="w-full" disabled={busy}>
          {busy && <LoaderCircle className="animate-spin" />} Sign in
        </Button>

        <p className="text-xs text-muted-foreground">
          Demo users: {DEMO_USERS.map((u, i) => (
            <span key={u}>
              <code className="font-mono">{u}</code>
              {i < DEMO_USERS.length - 1 ? ", " : ""}
            </span>
          ))}
          . Each sees only its own role&apos;s data.
        </p>
      </form>
    </div>
  );
}
