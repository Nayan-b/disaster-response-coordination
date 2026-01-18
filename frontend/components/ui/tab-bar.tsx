"use client";

/**
 * A small segmented tab bar.
 *
 * Hand-written rather than pulled from the registry: the panel needs a badge count and an attention
 * dot per tab (so a reviewer sees "3 rejected claims" without opening the tab), which is exactly the
 * part a generic Tabs primitive doesn't model. Roving focus and arrow-key navigation are kept.
 */

import { useRef } from "react";

import { cn } from "@/lib/utils";

export interface TabDefinition<T extends string> {
  id: T;
  label: string;
  /** Rendered as a muted pill after the label. Zero and undefined both render nothing. */
  count?: number;
  /** Amber pill — the count is a number of problems, not a number of items. */
  countTone?: "default" | "warn";
  /** Draws an attention dot — used when a tab holds something the reviewer must act on. */
  alert?: boolean;
  /** Native tooltip, so a bare count can say what it counts. */
  title?: string;
}

export function TabBar<T extends string>({
  tabs,
  value,
  onChange,
  className,
  "aria-label": ariaLabel,
}: {
  tabs: TabDefinition<T>[];
  value: T;
  onChange: (id: T) => void;
  className?: string;
  "aria-label": string;
}) {
  const listRef = useRef<HTMLDivElement>(null);

  const move = (delta: number) => {
    const index = tabs.findIndex((tab) => tab.id === value);
    const next = tabs[(index + delta + tabs.length) % tabs.length];
    onChange(next.id);
    const buttons = listRef.current?.querySelectorAll<HTMLButtonElement>("[role=tab]");
    buttons?.[tabs.indexOf(next)]?.focus();
  };

  return (
    <div
      ref={listRef}
      role="tablist"
      aria-label={ariaLabel}
      className={cn("flex items-center gap-1 rounded-lg bg-muted/60 p-1", className)}
      onKeyDown={(event) => {
        if (event.key === "ArrowRight") {
          event.preventDefault();
          move(1);
        } else if (event.key === "ArrowLeft") {
          event.preventDefault();
          move(-1);
        }
      }}
    >
      {tabs.map((tab) => {
        const selected = tab.id === value;
        return (
          <button
            key={tab.id}
            role="tab"
            type="button"
            aria-selected={selected}
            tabIndex={selected ? 0 : -1}
            title={tab.title}
            onClick={() => onChange(tab.id)}
            className={cn(
              "relative flex flex-1 items-center justify-center gap-1.5 rounded-md px-2.5 py-1.5 text-xs font-medium whitespace-nowrap transition-colors",
              "focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none",
              selected
                ? "bg-background text-foreground shadow-sm ring-1 ring-foreground/10"
                : "text-muted-foreground hover:text-foreground",
            )}
          >
            {tab.label}
            {tab.count ? (
              <span
                className={cn(
                  "rounded-full px-1.5 py-px text-[10px] tabular-nums",
                  tab.countTone === "warn"
                    ? "bg-amber-500/20 text-amber-700 dark:text-amber-300"
                    : selected
                      ? "bg-muted text-muted-foreground"
                      : "bg-muted-foreground/15",
                )}
              >
                {tab.count}
              </span>
            ) : null}
            {tab.alert ? (
              <span
                aria-hidden
                className="absolute top-1 right-1 size-1.5 animate-pulse rounded-full bg-amber-500"
              />
            ) : null}
          </button>
        );
      })}
    </div>
  );
}
