"use client";

import { Check, ChevronDown, CircleHelp, Code, ExternalLink, FilePen, Info, Lock, RotateCcw, TriangleAlert } from "lucide-react";
import { Fragment, useState } from "react";

import { DataTable } from "@/components/chat/data-table";
import { ResultChart } from "@/components/chat/result-chart";
import { Button } from "@/components/ui/button";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import type { ChatResponse } from "@/lib/types";
import { cn } from "@/lib/utils";

type AnswerProps = {
  response: ChatResponse;
  onRetry: () => void;
  onAsk: (text: string) => void;
  onDecide: (decision: "confirm" | "cancel") => void;
  latest: boolean;
};

export function Answer({ response, onRetry, onAsk, onDecide, latest }: AnswerProps) {
  if (response.type === "confirmation" && response.action) {
    return <ConfirmationCard card={response.action} intro={response.answer} active={latest} onDecide={onDecide} />;
  }
  if (response.type === "refusal") {
    return (
      <Notice icon={<Lock className="size-4" />} tone="muted">
        {response.answer}
      </Notice>
    );
  }
  if (response.type === "clarification") {
    return (
      <Notice icon={<CircleHelp className="size-4 text-primary" />}>
        <p>{response.answer}</p>
        {latest && response.options && response.options.length > 0 && (
          <div className="mt-2.5 flex flex-wrap gap-2">
            {response.options.map((option) => (
              <button
                key={option}
                type="button"
                onClick={() => onAsk(option)}
                className="rounded-full border bg-card px-3 py-1 text-sm transition-colors hover:bg-accent hover:text-accent-foreground"
              >
                {option}
              </button>
            ))}
          </div>
        )}
      </Notice>
    );
  }
  if (response.type === "error") {
    return (
      <Notice icon={<TriangleAlert className="size-4 text-destructive" />}>
        <p>{response.answer}</p>
        <Button variant="outline" size="sm" className="mt-2" onClick={onRetry}>
          <RotateCcw /> Try again
        </Button>
      </Notice>
    );
  }
  return <DataAnswer response={response} />;
}

function DataAnswer({ response }: { response: ChatResponse }) {
  const [view, setView] = useState<"chart" | "table">("chart");
  const showChart = response.type === "chart" && response.chart && view === "chart";
  const showTable = response.table && (response.type === "table" || (response.type === "chart" && view === "table"));

  return (
    <div className="space-y-3">
      <p className="leading-relaxed">{response.answer}</p>

      {response.type === "chart" && response.table && (
        <div className="flex gap-1 text-xs">
          {(["chart", "table"] as const).map((v) => (
            <button
              key={v}
              type="button"
              onClick={() => setView(v)}
              className={cn(
                "rounded-md px-2 py-1 capitalize text-muted-foreground transition-colors hover:text-foreground",
                view === v && "bg-muted text-foreground",
              )}
            >
              {v}
            </button>
          ))}
        </div>
      )}
      {showChart && response.chart && <ResultChart chart={response.chart} />}
      {showTable && response.table && <DataTable table={response.table} />}

      {response.caveats.length > 0 && (
        <ul className="space-y-1 text-xs text-muted-foreground">
          {response.caveats.map((c) => (
            <li key={c} className="flex gap-1.5">
              <Info className="mt-px size-3.5 shrink-0" />
              <span>{c}</span>
            </li>
          ))}
        </ul>
      )}

      {response.sql && (
        <Collapsible>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <CollapsibleTrigger className="group inline-flex items-center gap-1 hover:text-foreground">
              <Code className="size-3.5" /> Show SQL
              <ChevronDown className="size-3.5 transition-transform group-data-[state=open]:rotate-180" />
            </CollapsibleTrigger>
            {response.trace_url && (
              <a href={response.trace_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 hover:text-foreground">
                Trace <ExternalLink className="size-3" />
              </a>
            )}
          </div>
          <CollapsibleContent>
            <pre className="mt-2 overflow-x-auto rounded-lg bg-muted p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap">
              {response.sql}
            </pre>
          </CollapsibleContent>
        </Collapsible>
      )}
    </div>
  );
}

function ConfirmationCard({
  card,
  intro,
  active,
  onDecide,
}: {
  card: NonNullable<ChatResponse["action"]>;
  intro: string;
  active: boolean;
  onDecide: (decision: "confirm" | "cancel") => void;
}) {
  return (
    <div className="space-y-3">
      <p className="leading-relaxed">{intro}</p>
      <div className="rounded-xl border bg-card p-4">
        <p className="flex items-center gap-2 font-medium">
          <FilePen className="size-4 text-primary" /> {card.title}
        </p>
        <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-6 gap-y-1.5 text-sm">
          {card.summary.map(([label, value]) => (
            <Fragment key={label}>
              <dt className="text-muted-foreground">{label}</dt>
              <dd>{value}</dd>
            </Fragment>
          ))}
        </dl>
        {card.table && (
          <div className="mt-3">
            <DataTable table={card.table} />
          </div>
        )}
        {active ? (
          <div className="mt-4 flex gap-2">
            <Button onClick={() => onDecide("confirm")}>
              <Check /> Confirm
            </Button>
            <Button variant="outline" onClick={() => onDecide("cancel")}>
              Cancel
            </Button>
          </div>
        ) : (
          <p className="mt-3 text-xs text-muted-foreground">No longer active.</p>
        )}
      </div>
      <p className="text-xs text-muted-foreground">Nothing is changed until you confirm.</p>
    </div>
  );
}

function Notice({ icon, tone, children }: { icon: React.ReactNode; tone?: "muted"; children: React.ReactNode }) {
  return (
    <div className={cn("flex gap-2.5 leading-relaxed", tone === "muted" && "rounded-lg bg-muted/70 px-3 py-2.5 text-muted-foreground")}>
      <span className="mt-1 shrink-0">{icon}</span>
      <div>{children}</div>
    </div>
  );
}
