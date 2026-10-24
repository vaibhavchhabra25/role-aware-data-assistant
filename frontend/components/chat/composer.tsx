"use client";

import { ArrowUp } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

export function Composer({ disabled, onSend }: { disabled: boolean; onSend: (text: string) => void }) {
  const [text, setText] = useState("");
  const canSend = !disabled && text.trim().length > 0;

  const send = () => {
    if (!canSend) return;
    onSend(text.trim());
    setText("");
  };

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        send();
      }}
      className="relative rounded-2xl border bg-card shadow-xs focus-within:ring-3 focus-within:ring-ring/30"
    >
      <Textarea
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
            e.preventDefault();
            send();
          }
        }}
        placeholder="Ask a question…"
        aria-label="Your question"
        maxLength={2000}
        rows={1}
        className="max-h-40 min-h-12 resize-none border-0 bg-transparent py-3.5 pr-14 pl-4 shadow-none focus-visible:ring-0 dark:bg-transparent"
      />
      <Button type="submit" size="icon" disabled={!canSend} aria-label="Send" className="absolute right-2 bottom-2 rounded-full">
        <ArrowUp />
      </Button>
    </form>
  );
}
