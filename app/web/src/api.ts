export type Tier = "AUTO_CLEAR" | "AUTO_ESCALATE" | "BORDERLINE" | "HUMAN_REVIEW";
export type CaseStatus = "AUTOMATED" | "OPEN" | "AWAITING_SECOND" | "CLOSED";
/** Dual control: decision core vs independent rule check. */
export type Agreement = "AGREE" | "DISAGREE" | "CONFLICT" | "UNAVAILABLE";
export const ENGINE_FLAGS = ["ENGINES_DISAGREE", "ENGINES_CONFLICT", "ESCALATED_BY_POLICY"] as const;
export type EngineFlag = (typeof ENGINE_FLAGS)[number];

export interface EngineVerdict {
  decision: string | null;
  reason_code: string | null;
  reason: string;
  evidence: string[];
  customer_record_id: string | null;
  tier?: string;
  flags?: string[];
}

export interface Engines {
  core: EngineVerdict | null;
  check: EngineVerdict | null;
  agreement: Agreement;
  escalated_by_policy: boolean;
  reviewer_action: string | null;
}

export interface EngineSummary {
  agreement: Partial<Record<Agreement, number>>;
  compared: number;
  agreement_rate: number | null;
  escalated_by_policy: number;
  with_reviewer_action: number;
  pairs: Record<string, number>;
  mode: string;
}

export interface Health {
  ok: boolean;
  ready: boolean;
  status: "loading" | "ready" | "error";
  stage: string | null;
  progress: { done: number; total: number } | null;
  elapsed_seconds: number;
  error: string | null;
  engine_version: string;
  data: string;
}

export interface ReproReport {
  byte_identical: boolean;
  hits: number;
  unstable_hits: string[];
  decision_reproducibility: number | null;
  runs: { run: number; pythonhashseed: string; sha256: string; seconds: number }[];
}

export interface ReproState {
  report: ReproReport | null;
  at: string | null;
  running: boolean;
  error: string | null;
}

export interface Summary {
  engine_version: string;
  data: string;
  dataset: Record<string, number>;
  hits: number;
  decisions: Record<string, number>;
  tiers: Record<string, number>;
  reason_codes: Record<string, number>;
  flags: Record<string, number>;
  rule_stats: Record<string, Record<string, number>>;
  automation: { automated: number; needs_human: number; rate: number | null };
  engines?: EngineSummary;
  load_seconds: number;
  run_seconds: number;
  ran_at: string;
  reviewed_hits: number;
  case_status: Record<string, number>;
  repro: ReproState;
  eval: null | {
    unsafe_clearances: number;
    decision_score: number | null;
    decision_accuracy: number | null;
    labelled_hits: number;
    [k: string]: unknown;
  };
}

export interface Row {
  hit_id: string;
  alert_id: string;
  decision: string;
  reason_code: string;
  tier: Tier;
  customer_record_id: string | null;
  hit_name: string | null;
  customer_name: string | null;
  flags: string[];
  review: string | null;
  status: CaseStatus;
  final_decision: string | null;
  agreement?: Agreement | null;
  core_decision?: string | null;
  check_decision?: string | null;
  reviewer_action?: boolean;
}

export interface TraceRow {
  rule_id: string;
  facet: string;
  signal: "contradiction" | "agreement" | "neutral" | string;
  reason: string | null;
  summary: string;
  evidence: string[];
  weak: boolean;
  weak_why: string | null;
}

export type Rec = Record<string, unknown>;

export interface Person {
  key: string;
  record_ids: string[];
  records: Rec[];
  kyc: Rec[];
  kyc_ids: string[];
  note_ids: string[];
  notes: Rec[];
}

export interface ReviewRow {
  seq: number;
  hit_id: string;
  alert_id: string;
  engine_tier?: string;
  engine_decision: string;
  engine_reason_code?: string;
  action: string;
  decision: string;
  note: string;
  analyst: string;
  at: string;
  case_status?: CaseStatus;
}

export interface CaseInfo {
  status: CaseStatus;
  final_decision: string | null;
  analysts: string[];
  four_eyes?: boolean;
}

export interface Detail {
  decision: Omit<Row, "status" | "final_decision" | "review" | "hit_name" | "customer_name"> & {
    reason: string;
    evidence: string[];
    trace: TraceRow[];
    engines?: Engines;
  };
  hit: Rec;
  alert: Rec | null;
  resolution: null | { method: string; path: string[]; ambiguous: boolean; persons: Person[] };
  practice_label: Rec | null;
  reviews: ReviewRow[];
  case: CaseInfo;
}

export interface RecordLookup {
  id: string;
  kind: string;
  record: Rec;
  holders?: string[];
}

/** /api/overview: headline aggregates for the demo landing (same definitions as tools/scorecard.py). */
export interface Overview {
  hits: number;
  alerts: number;
  alerts_fully_cleared: number;
  tiers: Record<string, number>;
  decisions: Record<string, number>;
  fully_automated_pct: number;
  decided_by_engine_pct: number;
  cleared_pct: number;
  engine_agreement_pct: number;
  engines_disagree: number;
  engines_conflict: number;
  review_reasons: Record<string, number>;
  tier_reason_codes: Record<string, Record<string, number>>;
  run_seconds: number | null;
  load_seconds: number | null;
  dataset: Record<string, number> | null;
}

async function j<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let msg = await res.text();
    try {
      const body = JSON.parse(msg);
      if (body && typeof body.detail === "string") msg = body.detail;
    } catch {
      /* plain text */
    }
    throw new Error(msg || `${res.status}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  health: () => j<Health>("/api/health"),
  summary: () => j<Summary>("/api/summary"),
  overview: () => j<Overview>("/api/overview"),
  decisions: () => j<Row[]>("/api/decisions"),
  hit: (id: string) => j<Detail>(`/api/hits/${encodeURIComponent(id)}`),
  record: (id: string) => j<RecordLookup>(`/api/records/${encodeURIComponent(id)}`),
  reviews: () => j<ReviewRow[]>("/api/reviews"),
  rerun: () => j<Summary>("/api/run", { method: "POST" }),
  repro: () => j<ReproState>("/api/repro"),
  startRepro: () => j<ReproState>("/api/repro", { method: "POST" }),
  review: (id: string, body: { decision: string; note: string; analyst: string }) =>
    j<ReviewRow & { case: CaseInfo }>(`/api/hits/${encodeURIComponent(id)}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
};
