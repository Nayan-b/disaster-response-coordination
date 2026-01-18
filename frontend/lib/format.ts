/**
 * Display formatting.
 *
 * One rule runs through all of it: a missing value renders as an em dash, never as 0 and never as
 * an invented figure. The backend treats missing data as an error rather than an estimate, and the
 * UI has to hold that line too — a "0" in a population field reads as "nobody is affected".
 */

const INTEGER = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const COMPACT = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});

export const EMPTY = "—";

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

/** Full precision with thousands separators: 1,284,930. */
export function formatNumber(value: number | null | undefined): string {
  return isNumber(value) ? INTEGER.format(value) : EMPTY;
}

/** Space-constrained form: 1.3M. Always pair with the exact value in a `title`. */
export function formatCompactNumber(value: number | null | undefined): string {
  return isNumber(value) ? COMPACT.format(value) : EMPTY;
}

/** A claim's value with its unit, e.g. "1,284,930 people". */
export function formatMeasure(
  value: number | null | undefined,
  unit: string | null | undefined,
): string {
  if (!isNumber(value)) return EMPTY;
  const number = Number.isInteger(value) ? INTEGER.format(value) : String(value);
  return unit ? `${number} ${unit}` : number;
}

/** Square degrees, the unit GDACS extents come in. */
export function formatArea(sqDeg: number | null | undefined): string {
  if (!isNumber(sqDeg)) return EMPTY;
  const digits = sqDeg < 1 ? 3 : sqDeg < 100 ? 2 : 0;
  return `${sqDeg.toFixed(digits)} deg²`;
}

/** [minLon, minLat, maxLon, maxLat] → "72.10, 26.44 → 88.20, 30.11". */
export function formatBbox(bbox: [number, number, number, number] | null | undefined): string {
  if (!bbox || bbox.length !== 4) return EMPTY;
  const [minX, minY, maxX, maxY] = bbox;
  return `${minX.toFixed(2)}, ${minY.toFixed(2)} → ${maxX.toFixed(2)}, ${maxY.toFixed(2)}`;
}

export function formatClock(iso: string | null | undefined): string {
  if (!iso) return EMPTY;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return EMPTY;
  return date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function formatDate(iso: string | null | undefined): string {
  if (!iso) return EMPTY;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return EMPTY;
  return date.toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return EMPTY;
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return EMPTY;
  return `${formatDate(iso)}, ${date.toLocaleTimeString(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  })}`;
}

/** A date window as it appears on an event card: "2 Aug → 11 Aug 2026". */
export function formatWindow(from: string | null, to: string | null): string {
  if (!from && !to) return EMPTY;
  const start = from ? new Date(from) : null;
  const end = to ? new Date(to) : null;
  const short = (d: Date) => d.toLocaleDateString(undefined, { day: "numeric", month: "short" });
  if (start && end) {
    const sameYear = start.getFullYear() === end.getFullYear();
    return `${short(start)} → ${short(end)} ${sameYear ? end.getFullYear() : ""}`.trim();
  }
  return formatDate(from ?? to);
}

/** Elapsed wall-clock for a run: "0:04", "1:12". */
export function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}

/** Turn a graph node id into a human label: "population_overlay" → "Population Overlay". */
export function humanize(value: string): string {
  return value
    .split(/[_\-\s]+/)
    .filter(Boolean)
    .map((word) => word[0].toUpperCase() + word.slice(1))
    .join(" ");
}

/**
 * The run's terminal status, phrased for a responder rather than for a log line.
 * Returns null for statuses that are mid-run and shouldn't be announced as an outcome.
 */
export function describeOutcome(
  status: string | null | undefined,
): { tone: "positive" | "neutral" | "negative"; title: string; detail: string } | null {
  switch (status) {
    case "logged_approved":
      return {
        tone: "positive",
        title: "Approved and logged",
        detail:
          "Your decision is recorded in the action log. This system does not execute response actions — hand the approved brief to the dispatching team.",
      };
    case "logged_edited":
      return {
        tone: "positive",
        title: "Approved with your edits, and logged",
        detail:
          "The log records both the drafting engine and the actions you changed. No response action was executed.",
      };
    case "logged_rejected":
      return {
        tone: "neutral",
        title: "Rejected and logged",
        detail:
          "The recommendation was discarded. Your notes are on the record with the draft it replaced.",
      };
    case "logged_blocked":
    case "blocked_by_factcheck":
      return {
        tone: "negative",
        title: "Blocked before review",
        detail:
          "The fact-check rejected a load-bearing figure, so this draft never reached the approval gate. See the rejected claims under Evidence.",
      };
    case "no_events":
      return {
        tone: "neutral",
        title: "No events to triage",
        detail:
          "GDACS reported no flood events above the alert threshold in this window. That is a real result, not a failure.",
      };
    case "event_not_found":
      return {
        tone: "negative",
        title: "Event not in the window",
        detail:
          "The requested event is not among those GDACS lists for this lookback period. Widen the window or pick another event.",
      };
    case "no_extent":
      return {
        tone: "negative",
        title: "No affected-area polygon",
        detail:
          "GDACS published only a centroid for this event, so there is no extent to measure population or imagery over. Nothing was estimated in its place.",
      };
    default:
      return null;
  }
}
