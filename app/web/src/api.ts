export type Tier = "AUTO_CLEAR" | "AUTO_ESCALATE" | "BORDERLINE" | "HUMAN_REVIEW";
export type CaseStatus = "AUTOMATED" | "OPEN" | "AWAITING_SECOND" | "CLOSED";

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
  summary: () => j<Summary>("/api/summary"),
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
