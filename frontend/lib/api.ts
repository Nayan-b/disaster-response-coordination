/**
 * Client for the flood-response API.
 *
 * The run stream is consumed with `fetch` + a manual SSE parse rather than `EventSource`, because
 * EventSource cannot send headers, cannot be aborted cleanly, and gives no access to HTTP status —
 * all of which matter for surfacing a failed run instead of retrying it silently forever.
 */

import type {
  ApprovalDecision,
  ApprovalRequest,
  FloodEvent,
  Recommendation,
  RunState,
  TraceEvent,
} from "./types";

// 8088, not 8000 — see the port note in CLAUDE.md. `frontend/.env.local` normally supplies this;
// the fallback exists so a missing env file degrades to the right port rather than a stale one.
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8088";

export interface Health {
  ok: boolean;
  engine: string;
  llm_configured: boolean;
  at: string;
}

async function getJSON<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`${path} → HTTP ${response.status} ${response.statusText}`);
  }
  return (await response.json()) as T;
}

export function fetchHealth() {
  return getJSON<Health>("/api/health");
}

export interface EventListResponse {
  events: FloodEvent[];
  /** How many matched the window before the cap — so the picker can say "10 of 23". */
  total_matched?: number;
  truncated?: boolean;
  unavailable?: string;
  source?: string;
}

/**
 * `levels` is a comma-separated list ("Red,Orange"). It is passed as a string rather than an array so
 * callers can use it directly as an effect dependency — an array literal would change identity on
 * every render and refetch forever.
 *
 * Note the backend falls back to Orange+Red when the list resolves to empty, so the caller must not
 * send an empty selection expecting "nothing".
 */
export function fetchEvents(days: number, limit: number, levels: string) {
  return getJSON<EventListResponse>(
    `/api/events?days=${days}&limit=${limit}&levels=${encodeURIComponent(levels)}`,
  );
}

export async function startRun(body: {
  event_id?: number | null;
  episode_id?: number | null;
  lookback_days?: number;
  /** Must match the levels the picker is showing, or a listed event won't be found by the run. */
  alert_levels?: string[];
  reviewer?: string;
}) {
  const response = await fetch(`${API_BASE}/api/runs`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) throw new Error(`start run → HTTP ${response.status}`);
  return (await response.json()) as {
    thread_id: string;
    stream_url: string;
    engine: string;
  };
}

export interface StreamHandlers {
  onTrace?: (event: TraceEvent) => void;
  onNodeComplete?: (node: string) => void;
  onState?: (state: RunState) => void;
  onApprovalRequest?: (request: ApprovalRequest) => void;
  onError?: (message: string) => void;
  onDone?: (info: { status: string | null; awaiting_approval: boolean }) => void;
}

/** Consume a run's SSE stream. Returns a function that aborts it. */
export function streamRun(threadId: string, handlers: StreamHandlers): () => void {
  const controller = new AbortController();

  (async () => {
    let response: Response;
    try {
      response = await fetch(`${API_BASE}/api/runs/${threadId}/stream`, {
        headers: { accept: "text/event-stream" },
        signal: controller.signal,
        cache: "no-store",
      });
    } catch (error) {
      if (!controller.signal.aborted) {
        handlers.onError?.(`could not open stream: ${(error as Error).message}`);
      }
      return;
    }

    if (!response.ok || !response.body) {
      handlers.onError?.(`stream → HTTP ${response.status} ${response.statusText}`);
      return;
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE frames are separated by a blank line — which is CRLFCRLF from sse-starlette, not
        // LFLF. Splitting on "\n\n" alone silently matches nothing and no event ever dispatches.
        const frames = buffer.split(/\r?\n\r?\n/);
        buffer = frames.pop() ?? "";
        for (const frame of frames) dispatchFrame(frame, handlers);
      }
      if (buffer.trim()) dispatchFrame(buffer, handlers);
    } catch (error) {
      if (!controller.signal.aborted) {
        handlers.onError?.(`stream interrupted: ${(error as Error).message}`);
      }
    }
  })();

  return () => controller.abort();
}

function dispatchFrame(frame: string, handlers: StreamHandlers) {
  let eventName = "message";
  const dataLines: string[] = [];

  for (const rawLine of frame.split("\n")) {
    const line = rawLine.replace(/\r$/, "");
    if (line.startsWith("event:")) eventName = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trimStart());
  }
  if (dataLines.length === 0) return;

  let payload: unknown;
  try {
    payload = JSON.parse(dataLines.join("\n"));
  } catch {
    return; // a malformed frame is skipped, not fatal
  }

  switch (eventName) {
    case "trace":
      handlers.onTrace?.(payload as TraceEvent);
      break;
    case "node_complete":
      handlers.onNodeComplete?.((payload as { node: string }).node);
      break;
    case "state":
      handlers.onState?.(payload as RunState);
      break;
    case "approval_request":
      handlers.onApprovalRequest?.(payload as ApprovalRequest);
      break;
    case "error":
      handlers.onError?.((payload as { message: string }).message);
      break;
    case "done":
      handlers.onDone?.(payload as { status: string | null; awaiting_approval: boolean });
      break;
  }
}

export async function submitDecision(
  threadId: string,
  body: {
    decision: "approved" | "edited" | "rejected";
    reviewer: string;
    notes?: string | null;
    edited_actions?: unknown[] | null;
  },
) {
  const response = await fetch(`${API_BASE}/api/runs/${threadId}/decision`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(
      (payload as { detail?: string }).detail ?? `decision → HTTP ${response.status}`,
    );
  }
  return payload as DecisionResponse;
}

export interface DecisionResponse {
  thread_id: string;
  /** "logged_approved" | "logged_edited" | "logged_rejected". */
  status: string;
  approval: ApprovalDecision;
  /** Carries the reviewer's edits, so an edited brief renders from the server's copy. */
  recommendation: Recommendation | null;
  /** The last few trace lines the resume produced — appended to the live trace. */
  trace: TraceEvent[];
  notice: string;
}
