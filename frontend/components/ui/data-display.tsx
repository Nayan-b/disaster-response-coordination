"use client";

/**
 * The three primitives every panel in this dashboard formats its data with.
 *
 * `Stat` — a headline figure with the provenance underneath it, because a number without its source
 *   is exactly what this system is built not to produce.
 * `Field` — a label/value row for reference data.
 * `EmptyState` — what a panel shows when there is nothing yet, phrased so the reviewer knows whether
 *   that means "not run" or "genuinely nothing".
 */

import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type Tone = "default" | "warn" | "danger" | "positive" | "muted";

const TONE_VALUE: Record<Tone, string> = {
  default: "text-foreground",
  warn: "text-amber-600 dark:text-amber-400",
  danger: "text-red-600 dark:text-red-400",
  positive: "text-emerald-600 dark:text-emerald-400",
  muted: "text-muted-foreground",
};

export function Stat({
  label,
  value,
  unit,
  note,
  tone = "default",
  title,
  icon,
}: {
  label: string;
  value: ReactNode;
  unit?: string;
  note?: ReactNode;
  tone?: Tone;
  /** Native tooltip — used to carry the exact figure behind a compacted one. */
  title?: string;
  icon?: ReactNode;
}) {
  return (
    <div className="min-w-0 rounded-lg bg-card px-3 py-2.5 ring-1 ring-foreground/10">
      <div className="flex items-center gap-1.5 text-[11px] font-medium tracking-wide text-muted-foreground uppercase">
        {icon}
        <span className="truncate">{label}</span>
      </div>
      <div
        title={title}
        className={cn(
          "mt-1 flex items-baseline gap-1 text-xl leading-none font-semibold tabular-nums",
          TONE_VALUE[tone],
        )}
      >
        <span className="truncate">{value}</span>
        {unit ? <span className="text-xs font-normal text-muted-foreground">{unit}</span> : null}
      </div>
      {note ? (
        <div className="mt-1.5 text-[11px] leading-snug text-muted-foreground">{note}</div>
      ) : null}
    </div>
  );
}

export function Field({
  label,
  value,
  mono,
  tone = "default",
  title,
}: {
  label: string;
  value: ReactNode;
  mono?: boolean;
  tone?: Tone;
  title?: string;
}) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <dt className="shrink-0 text-xs text-muted-foreground">{label}</dt>
      <dd
        title={title}
        className={cn(
          "min-w-0 truncate text-right text-xs",
          mono && "font-mono tabular-nums",
          TONE_VALUE[tone],
        )}
      >
        {value}
      </dd>
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  detail,
}: {
  icon?: ReactNode;
  title: string;
  detail?: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-10 text-center">
      {icon ? <div className="text-muted-foreground/50">{icon}</div> : null}
      <p className="text-sm font-medium text-muted-foreground">{title}</p>
      {detail ? (
        <p className="max-w-xs text-xs leading-relaxed text-muted-foreground/80">{detail}</p>
      ) : null}
    </div>
  );
}

/** Section heading used inside the scrolling panels. */
export function SectionTitle({
  children,
  action,
}: {
  children: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-center justify-between gap-3">
      <h3 className="text-xs font-semibold tracking-wide text-muted-foreground uppercase">
        {children}
      </h3>
      {action}
    </div>
  );
}
