/** Shapes mirrored from the FastAPI service (`backend/app/api.py`). */

export type TraceLevel = "info" | "warning" | "error";

export interface TraceEvent {
  node: string;
  message: string;
  at: string;
  level: TraceLevel;
}

export interface FloodEvent {
  event_id: number;
  episode_id: number | null;
  name: string;
  country: string | null;
  iso3_codes: string[];
  alert_level: string | null;
  episode_alert_level: string | null;
  from_date: string | null;
  to_date: string | null;
  severity: string | null;
  severity_note?: string;
  report_url: string | null;
}

export interface Claim {
  claim_id: string;
  text: string;
  value: number | null;
  unit: string | null;
  source_call_id: string | null;
  verified: boolean;
  rejection_reason: string | null;
}

export interface AllocationAction {
  priority: number;
  action: string;
  rationale: string;
  supporting_claim_ids: string[];
}

export interface Recommendation {
  headline: string;
  situation: string;
  actions: AllocationAction[];
  caveats: string[];
  /** "claude-opus-5" or "rule-based" — surfaced in the UI so the reviewer knows what drafted this. */
  drafted_by: string;
  drafted_at: string;
  requires_human_approval: boolean;
}

export interface ToolCallSummary {
  call_id: string;
  tool: string;
  arguments: Record<string, unknown>;
  ok: boolean;
  called_at: string;
  reason: string | null;
}

export interface TileLayer {
  layer: string;
  date: string;
  url_template: string;
  tile_matrix_set: string;
  max_zoom: number;
  attribution: string;
}

export interface ExtentInfo {
  bbox: [number, number, number, number];
  geometry_type: string;
  area_sq_deg: number;
  polygon_label: string | null;
}

export interface PopulationInfo {
  population: number;
  countries_covered: string[];
  countries_missing: string[];
  is_partial: boolean;
  raster_year: number;
  resolution: string;
  pixels_in_polygon: number;
  per_country: Record<string, number>;
}

export interface RunState {
  event: FloodEvent | null;
  extent: ExtentInfo | null;
  population: PopulationInfo | null;
  imagery: { collection: string; granule_count: number; matched: number | null } | null;
  tiles: TileLayer | null;
  claims: Claim[] | null;
  recommendation: Recommendation | null;
  approval: ApprovalDecision | null;
  unavailable: Record<string, string>;
  engine: string | null;
  status: string | null;
  tool_calls: ToolCallSummary[];
}

export interface ApprovalRequest {
  type: "approval_request";
  event: FloodEvent | null;
  recommendation: Recommendation | null;
  verified_claims: Claim[];
  rejected_claims: Claim[];
  unavailable: Record<string, string>;
  drafted_by: string | null;
  notice: string;
}

export interface ApprovalDecision {
  decision: "approved" | "edited" | "rejected";
  reviewer: string;
  decided_at: string;
  notes: string | null;
  edited_actions: AllocationAction[] | null;
}
