import { useEffect, useRef, type RefObject } from "react";
import { ENGINE_FLAGS, type Row, type Tier } from "./api";
import { DECISION_LABEL, ENGINE_FLAG_LABEL, humanFlag, reasonLabel, STATUS_LABEL, TIER_BY_ID, TIERS, whyTier } from "./labels";
import { Popover, StatusPill } from "./ui";

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

/** Reason, flag, status and dual-control filters behind one "Filters" button. */
function FilterMenu({ filters, setFilters, reasons, flags, engineCounts }: {
  filters: Filters; setFilters: (f: Filters) => void; reasons: string[]; flags: string[]; engineCounts: Record<string, number>;
}) {
  const active = [filters.reason, filters.flag, filters.status, filters.engine].filter(Boolean).length;
  return (
    <div className="filter-bar">
      <Popover className="filter-pop" buttonClass={`btn filter-btn ${active ? "has" : ""}`}
        label={<>Filters{active > 0 && <span className="filter-count" aria-label={`${active} active`}>{active}</span>}</>}>
        {() => (
          <div className="filter-panel">
            <label className="field">
              <span>Reason</span>
              <select value={filters.reason} onChange={(e) => setFilters({ ...filters, reason: e.target.value })}>
                <option value="">Any reason</option>
                {reasons.map((r) => <option key={r} value={r}>{reasonLabel(r)}</option>)}
              </select>
            </label>
            <label className="field">
              <span>Weak-evidence flag</span>
              <select value={filters.flag} onChange={(e) => setFilters({ ...filters, flag: e.target.value })}>
                <option value="">Any flag</option>
                {flags.map((f) => <option key={f} value={f}>{humanFlag(f)}</option>)}
              </select>
            </label>
            <label className="field">
              <span>Case status</span>
              <select value={filters.status} onChange={(e) => setFilters({ ...filters, status: e.target.value })}>
                <option value="">Any status</option>
                {Object.entries(STATUS_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </select>
            </label>
            <div className="field">
              <span>Two engines</span>
              <div className="engine-filter" role="group" aria-label="Dual-control filter">
                {ENGINE_FLAGS.map((f) => (
                  <button key={f} className={`chip ef ${filters.engine === f ? "on" : ""}`} aria-pressed={filters.engine === f}
                    title={humanFlag(f)} onClick={() => setFilters({ ...filters, engine: filters.engine === f ? "" : f })}>
                    {ENGINE_FLAG_LABEL[f].replace(/ \(.*\)$/, "")} <span className="count">{engineCounts[f] ?? 0}</span>
                  </button>
                ))}
              </div>
            </div>
          </div>
        )}
      </Popover>
      {active > 0 && (
        <button className="link clear-link" onClick={() => setFilters({ ...filters, reason: "", flag: "", status: "", engine: "" })}>Clear</button>
      )}
    </div>
  );
}

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
            <span className="tab-label">{t.short}</span>
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
        <input ref={searchRef} className="search" placeholder="Search hit, name, reason…  ( / )" value={filters.q}
          onChange={(e) => setFilters({ ...filters, q: e.target.value })} aria-label="Search" />
        <FilterMenu filters={filters} setFilters={setFilters} reasons={reasons} flags={flags} engineCounts={engineCounts} />
      </div>
      <div className="list-meta small muted">
        {rows.length} {rows.length === 1 ? "hit" : "hits"}
        {tier !== "ALL" && <> · {TIER_BY_ID[tier].who}</>}
      </div>
      <ul className="list" ref={listRef}>
        {rows.map((r) => {
          const sel = selected === r.hit_id;
          return (
            <li key={r.hit_id} data-id={r.hit_id} className={`t-${r.tier} ${sel ? "sel" : ""}`}
              onClick={() => onSelect(r.hit_id)} aria-selected={sel} title={whyTier(r)}>
              <div className="row1">
                {tier === "ALL" && <span className="dot tier-dot" aria-label={TIER_BY_ID[r.tier].short} />}
                <span className="mono strong">{r.hit_id}</span>
                <span className="grow" />
                <StatusPill status={r.status} />
              </div>
              <div className="row2">
                <span>{r.customer_name ?? "—"}</span>
                <span className="muted"> vs {r.hit_name ?? "—"}</span>
              </div>
              {sel && (
                <div className="row3">
                  {whyTier(r)}
                  {r.review && r.review !== r.decision && <> Analyst: <strong>{DECISION_LABEL[r.review] ?? r.review}</strong>.</>}
                </div>
              )}
            </li>
          );
        })}
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
