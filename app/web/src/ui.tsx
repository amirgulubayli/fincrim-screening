import { useEffect, useState } from "react";
import { api, type Agreement, type CaseStatus, type RecordLookup, type Tier } from "./api";
import { AGREEMENT_LABEL, DECISION_LABEL, STATUS_LABEL, TIER_BY_ID } from "./labels";

/** AGREE / DISAGREE / CONFLICT between the decision core and the independent rule check. */
export function AgreementBadge({ status, small = false }: { status: Agreement; small?: boolean }) {
  const l = AGREEMENT_LABEL[status] ?? { short: status, long: status };
  return (
    <span className={`agree-badge ag-${status} ${small ? "sm" : ""}`} title={l.long}>
      {small ? l.short : status === "UNAVAILABLE" ? "ONE ENGINE" : status}
    </span>
  );
}

export function fmt(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (Array.isArray(v)) return v.length ? v.join("; ") : "—";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

export function Badge({ decision }: { decision: string }) {
  return <span className={`badge badge-${decision}`}>{DECISION_LABEL[decision] ?? decision}</span>;
}

export function TierPill({ tier, long = false }: { tier: Tier; long?: boolean }) {
  const t = TIER_BY_ID[tier];
  return (
    <span className={`tier-pill t-${tier}`} title={t?.who}>
      <span className="dot" />
      {long ? t?.label : t?.short}
    </span>
  );
}

export function StatusPill({ status }: { status: CaseStatus }) {
  return <span className={`status s-${status}`}>{STATUS_LABEL[status] ?? status}</span>;
}

const cache = new Map<string, Promise<RecordLookup>>();

function lookup(id: string): Promise<RecordLookup> {
  let p = cache.get(id);
  if (!p) {
    p = api.record(id);
    p.catch(() => cache.delete(id));
    cache.set(id, p);
  }
  return p;
}

const KIND_LABEL: Record<string, string> = {
  customer: "Customer record",
  kyc_check: "KYC check",
  note: "Support note",
  search_result: "Search result",
  hit: "Watchlist hit",
  alert: "Screening alert",
  account: "Account",
  kyc_applicant: "KYC applicant",
};

function RecordCard({ id, onClose }: { id: string; onClose: () => void }) {
  const [rec, setRec] = useState<RecordLookup | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    setRec(null);
    setErr(null);
    lookup(id).then(setRec).catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [id]);
  return (
    <div className="record-card" role="region" aria-label={`Record ${id}`}>
      <div className="record-head">
        <span>
          <span className="mono strong">{id}</span>{" "}
          <span className="muted">{rec ? KIND_LABEL[rec.kind] ?? rec.kind : err ? "" : "loading…"}</span>
        </span>
        <button className="link" onClick={onClose} aria-label="Close record">close</button>
      </div>
      {err && <div className="error small">{err}</div>}
      {rec && (
        <dl className="kv">
          {Object.entries(rec.record)
            .filter(([k, v]) => k !== "extra" && v !== null && v !== "" && !(Array.isArray(v) && !v.length))
            .map(([k, v]) => (
              <div key={k} className={k === "text" || k === "snippet" || k === "profile_notes" || k === "details" ? "wide" : ""}>
                <dt>{k.replace(/_/g, " ")}</dt>
                <dd>{fmt(v)}</dd>
              </div>
            ))}
          {rec.holders && rec.holders.length > 1 && (
            <div className="wide"><dt>holders</dt><dd>{rec.holders.join(", ")}</dd></div>
          )}
        </dl>
      )}
    </div>
  );
}

/** Record-id chips; clicking one expands the underlying record (read-only) below the row. */
export function EvidenceList({ ids, empty = "none", highlight }: { ids: string[]; empty?: string; highlight?: Set<string> }) {
  const [open, setOpen] = useState<string | null>(null);
  const uniq = Array.from(new Set(ids.filter(Boolean)));
  if (!uniq.length) return <span className="muted small">{empty}</span>;
  return (
    <div className="evidence">
      <div className="chips">
        {uniq.map((id) => (
          <button
            key={id}
            className={`chip mono ${open === id ? "open" : ""} ${highlight?.has(id) ? "decisive" : ""}`}
            aria-expanded={open === id}
            title={highlight?.has(id) ? "Cited as deciding evidence — click to view" : "Click to view the record"}
            onClick={() => setOpen(open === id ? null : id)}
          >
            {id}
          </button>
        ))}
      </div>
      {open && <RecordCard id={open} onClose={() => setOpen(null)} />}
    </div>
  );
}
