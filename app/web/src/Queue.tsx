import { useEffect, useRef, type RefObject } from "react";
import { ENGINE_FLAGS, type Row, type Tier } from "./api";
import { ENGINE_FLAG_LABEL, humanFlag, reasonLabel, STATUS_LABEL, TIER_BY_ID, TIERS, whyTier } from "./labels";
import { AgreementBadge, Badge, StatusPill } from "./ui";

export type TierTab = Tier | "ALL";

export interface Filters {
  q: string;
  reason: string;
  flag: string;
  status: string;
  engine: string; // ENGINES_DISAGREE | ENGINES_CONFLICT | ESCALATED_BY_POLICY
}

const EMPTY_HINT: Record<TierTab, string> = {
  HUMAN_REVIEW: "Nothing is waiting for an analyst.",
  BORDERLINE: "No automated decision rests on weak evidence in this run.",
  AUTO_ESCALATE: "No hit had strong enough agreement to escalate automatically.",
  AUTO_CLEAR: "No hit had a hard contradiction, so nothing was cleared automatically. When in doubt, the engine sends hits to review.",
  ALL: "No hits in this run.",
};

export default function Queue({ rows, counts, engineCounts, tier, setTier, filters, setFilters, reasons, flags, selected, onSelect, searchRef }: {
  rows: Row[];
  counts: Record<string, number>;
  engineCounts: Record<string, number>;
  tier: TierTab;
  setTier: (t: TierTab) => void;
  filters: Filters;
  setFilters: (f: Filters) => void;
  reasons: string[];
  flags: string[];
  selected: string | null;
  onSelect: (id: string) => void;
  searchRef: RefObject<HTMLInputElement | null>;
}) {
  const listRef = useRef<HTMLUListElement>(null);
  useEffect(() => {
    if (!selected) return;
    listRef.current?.querySelector<HTMLElement>(`[data-id="${CSS.escape(selected)}"]`)?.scrollIntoView({ block: "nearest" });
  }, [selected]);
  const filtered = filters.q || filters.reason || filters.flag || filters.status || filters.engine;
  return (
    <aside className="queue" aria-label="Review queue">
      <div className="tabs" role="tablist" aria-label="Tiers">
        {TIERS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tier === t.id}
            className={`tab t-${t.id} ${tier === t.id ? "active" : ""}`} onClick={() => setTier(t.id)}
            title={`${t.label} — ${t.who} (key ${t.key})`}>
            <span className="dot" />
            <span className="tab-label">{t.label}</span>
            <span className="count">{counts[t.id] ?? 0}</span>
          </button>
        ))}
        <button role="tab" aria-selected={tier === "ALL"} className={`tab all ${tier === "ALL" ? "active" : ""}`}
          onClick={() => setTier("ALL")} title="All hits (key 0)">
          <span className="tab-label">All hits</span>
          <span className="count">{counts.ALL ?? 0}</span>
        </button>
      </div>
      <div className="filters">
        <input ref={searchRef} className="search" placeholder="Search hit, alert, name, reason…  ( / )" value={filters.q}
          onChange={(e) => setFilters({ ...filters, q: e.target.value })} aria-label="Search" />
        <div className="filter-row">
          <select value={filters.reason} onChange={(e) => setFilters({ ...filters, reason: e.target.value })} aria-label="Reason code">
            <option value="">Any reason</option>
            {reasons.map((r) => <option key={r} value={r}>{reasonLabel(r)}</option>)}
          </select>
          <select value={filters.flag} onChange={(e) => setFilters({ ...filters, flag: e.target.value })} aria-label="Flag">
            <option value="">Any flag</option>
            {flags.map((f) => <option key={f} value={f}>{f}</option>)}
          </select>
          <select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })} aria-label="Case status">
            <option value="">Any status</option>
            {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </select>
        </div>
        <div className="engine-filter" role="group" aria-label="Dual-control filter">
          <span className="small muted">Two engines:</span>
          {ENGINE_FLAGS.map((f) => (
            <button key={f} className={`chip ef ef-${f} ${filters.engine === f ? "on" : ""}`} aria-pressed={filters.engine === f}
              title={humanFlag(f)} onClick={() => setFilters({ ...filters, engine: filters.engine === f ? "" : f })}>
              {ENGINE_FLAG_LABEL[f].replace(/ \(.*\)$/, "")} <span className="count">{engineCounts[f] ?? 0}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="list-meta small muted">
        {rows.length} {rows.length === 1 ? "hit" : "hits"}
        {tier !== "ALL" && <> · {TIER_BY_ID[tier].who}</>}
      </div>
      <ul className="list" ref={listRef}>
        {rows.map((r) => (
          <li key={r.hit_id} data-id={r.hit_id} className={`t-${r.tier} ${selected === r.hit_id ? "sel" : ""}`}
            onClick={() => onSelect(r.hit_id)} aria-selected={selected === r.hit_id}>
            <div className="row1">
              <span className="mono strong">{r.hit_id}</span>
              <Badge decision={r.decision} />
              {r.review && r.review !== r.decision && (
                <span className="small muted" title="Analyst decision">→ <Badge decision={r.review} /></span>
              )}
              <span className="grow" />
              {r.agreement && r.agreement !== "AGREE" && <AgreementBadge status={r.agreement} small />}
              {r.flags.includes("ESCALATED_BY_POLICY") && <span className="policy-tag" title={humanFlag("ESCALATED_BY_POLICY")}>policy</span>}
              <StatusPill status={r.status} />
            </div>
            <div className="row2">
              <span>{r.customer_name ?? "—"}</span>
              <span className="muted"> vs {r.hit_name ?? "—"}</span>
            </div>
            <div className="row3" title={r.flags.map(humanFlag).join("; ")}>{whyTier(r)}</div>
          </li>
        ))}
        {!rows.length && (
          <li className="empty">
            <strong>{filtered ? "No hits match these filters." : "Queue is empty."}</strong>
            <span className="muted small">{filtered ? "Clear the search or filters to see more." : EMPTY_HINT[tier]}</span>
          </li>
        )}
      </ul>
      <div className="keys small muted">
        <kbd>j</kbd>/<kbd>k</kbd> next/prev · <kbd>1</kbd>–<kbd>4</kbd> tiers · <kbd>0</kbd> all · <kbd>/</kbd> search
      </div>
    </aside>
  );
}
