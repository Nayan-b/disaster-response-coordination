"use client";

/**
 * Light/dark toggle.
 *
 * Hand-rolled rather than pulling in a theme provider: the whole mechanism is one class on <html>,
 * and the flash-of-wrong-theme is handled by the inline script in `app/layout.tsx` that runs before
 * paint. A dependency would only re-implement those two things.
 *
 * The stored preference is read with `useSyncExternalStore` rather than copied into state inside an
 * effect. localStorage *is* the external store here — reading it through the subscription keeps the
 * server snapshot ("system") and the client's real value from fighting during hydration.
 */

import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useSyncExternalStore } from "react";

import { cn } from "@/lib/utils";

type Mode = "light" | "dark" | "system";

const STORAGE_KEY = "flood-theme";
const CHANGE_EVENT = "flood-theme-change";

const MODES: { id: Mode; label: string; icon: typeof Sun }[] = [
  { id: "light", label: "Light", icon: Sun },
  { id: "system", label: "System", icon: Monitor },
  { id: "dark", label: "Dark", icon: Moon },
];

function prefersDark() {
  return window.matchMedia("(prefers-color-scheme: dark)").matches;
}

function subscribe(onChange: () => void) {
  window.addEventListener("storage", onChange);
  window.addEventListener(CHANGE_EVENT, onChange);
  return () => {
    window.removeEventListener("storage", onChange);
    window.removeEventListener(CHANGE_EVENT, onChange);
  };
}

function readMode(): Mode {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  } catch {
    return "system";
  }
}

function applyMode(mode: Mode) {
  document.documentElement.classList.toggle(
    "dark",
    mode === "dark" || (mode === "system" && prefersDark()),
  );
  try {
    if (mode === "system") localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, mode);
  } catch {
    // A blocked storage API costs the preference across reloads, not the toggle itself.
  }
  window.dispatchEvent(new Event(CHANGE_EVENT));
}

export function ThemeToggle() {
  const mode = useSyncExternalStore(subscribe, readMode, () => "system" as Mode);

  // Following the OS only matters while the reader is on "system".
  useEffect(() => {
    if (mode !== "system") return;
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => document.documentElement.classList.toggle("dark", media.matches);
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [mode]);

  return (
    <div
      role="radiogroup"
      aria-label="Color theme"
      className="flex items-center gap-0.5 rounded-lg bg-muted/60 p-0.5"
    >
      {MODES.map(({ id, label, icon: Icon }) => (
        <button
          key={id}
          type="button"
          role="radio"
          aria-checked={mode === id}
          aria-label={label}
          title={label}
          onClick={() => applyMode(id)}
          className={cn(
            "flex size-6 items-center justify-center rounded-md transition-colors",
            "focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none",
            mode === id
              ? "bg-background text-foreground shadow-sm ring-1 ring-foreground/10"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          <Icon className="size-3.5" />
        </button>
      ))}
    </div>
  );
}
