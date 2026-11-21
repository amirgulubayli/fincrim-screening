export type Tier = "AUTO_CLEAR" | "AUTO_ESCALATE" | "BORDERLINE" | "HUMAN_REVIEW";

export interface Summary {
  engine_version: string;
  data: string;
  dataset: Record<string, number>;
  hits: number;
  decisions: Record<string, number>;
  tiers: Record<string, number>;
  load_seconds: number;
  run_seconds: number;
  ran_at: string;
  reviewed_hits: number;
  eval: null | { unsafe_clearances: number; decision_score: number | null; labelled_hits: number; [k: string]: unknown };
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
}

export interface TraceRow {
  rule_id: string;
  facet: string;
  signal: string;
  reason: string | null;
  summary: string;
  evidence: string[];
  weak: boolean;
  weak_why: string | null;
}

type Rec = Record<string, unknown>;

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
  hit_id: string;
  action: string;
  decision: string;
  engine_decision: string;
  note: string;
  analyst: string;
  at: string;
}

export interface Detail {
  decision: Row & { reason: string; evidence: string[]; trace: TraceRow[] };
  hit: Rec;
  alert: Rec | null;
  resolution: null | { method: string; path: string[]; ambiguous: boolean; persons: Person[] };
  practice_label: Rec | null;
  reviews: ReviewRow[];
}

async function j<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`);
  return res.json() as Promise<T>;
}

export const api = {
  summary: () => j<Summary>("/api/summary"),
  decisions: () => j<Row[]>("/api/decisions"),
  hit: (id: string) => j<Detail>(`/api/hits/${encodeURIComponent(id)}`),
  rerun: () => j<Summary>("/api/run", { method: "POST" }),
  review: (id: string, body: { decision: string; note: string; analyst: string }) =>
    j<ReviewRow>(`/api/hits/${encodeURIComponent(id)}/review`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
};
