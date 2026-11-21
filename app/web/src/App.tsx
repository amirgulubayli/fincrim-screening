import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, type ReproState, type Row, type Summary } from "./api";
import Audit from "./Audit";
import DetailPane from "./Detail";
import { TIERS } from "./labels";
import Policy from "./Policy";
import Queue, { type Filters, type TierTab } from "./Queue";
import Terminal from "./Terminal";

type View = "queue" | "audit" | "policy";

function pct(x: number | null | undefined): string {
  return x === null || x === undefined ? "—" : `${(x * 100).toFixed(1)}%`;
}

function Kpi({ label, value, sub, tone = "", title }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string; title?: string }) {
  return (
    <div className={`kpi ${tone}`} title={title}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {sub && <div className="kpi-sub">{sub}</div>}
    </div>
  );
}

function ReproKpi({ r, onRun }: { r: ReproState | undefined; onRun: () => void }) {
  if (r?.running) return <Kpi label="Reproducibility" value={<span className="spin">running…</span>} sub="5 fresh runs, different hash seeds" />;
  const rep = r?.report;
  if (!rep)
    return (
      <Kpi label="Reproducibility" value={<span className="muted">not verified</span>}
        sub={<button className="link" onClick={onRun}>run 5× check</button>} title={r?.error ?? undefined} />
    );
  return (
    <Kpi
      label="Reproducibility"
      tone={rep.byte_identical ? "ok" : "bad"}
      value={rep.byte_identical ? `${rep.runs.length}/${rep.runs.length} identical` : "NOT identical"}
      sub={<>{r?.at ? new Date(r.at).toLocaleString() : ""} · <button className="link" onClick={onRun}>re-check</button></>}
      title={rep.runs.map((x) => `run ${x.run} seed ${x.pythonhashseed}: ${x.sha256.slice(0, 16)}…`).join("\n")}
    />
  );
}

function Header({ s, view, setView, onRerun, running, onRepro }: {
  s: Summary | null; view: View; setView: (v: View) => void; onRerun: () => void; running: boolean; onRepro: () => void;
}) {
  const ev = s?.eval;
  const auto = s?.automation;
  const open = (s?.case_status?.OPEN ?? 0) + (s?.case_status?.AWAITING_SECOND ?? 0);
  return (
    <header className="topbar">
      <div className="bar-row">
        <div className="brand">
          <strong>Sanctions screening — analyst review</strong>
          <span className="muted small">
            engine {s?.engine_version ?? "—"} · {s?.data ?? ""} · {s?.hits ?? "—"} hits decided in {s ? s.run_seconds : "—"}s (data load {s ? s.load_seconds : "—"}s)
          </span>
        </div>
        <nav className="nav" aria-label="Views">
          {([["queue", "Review queue"], ["audit", "Audit trail"], ["policy", "Policy & controls"]] as const).map(([id, label]) => (
            <button key={id} className={view === id ? "on" : ""} aria-current={view === id ? "page" : undefined} onClick={() => setView(id)}>
              {label}
            </button>
          ))}
        </nav>
        <button className="btn" onClick={onRerun} disabled={running}>{running ? "Running engine…" : "Re-run engine"}</button>
      </div>
      <div className="kpis">
        <Kpi label="Automation rate" value={pct(auto?.rate)}
          sub={auto ? `${auto.automated} automated · ${auto.needs_human} need a person` : ""}
          title="Share of hits decided on solid evidence with no person needed (auto-cleared + auto-escalated)" />
        <Kpi label="Open cases" value={open} sub={`${s?.case_status?.AWAITING_SECOND ?? 0} awaiting 2nd reviewer · ${s?.reviewed_hits ?? 0} reviewed`} />
        <Kpi
          label="Unsafe clearances"
          value={ev ? ev.unsafe_clearances : "n/a"}
          tone={ev ? (ev.unsafe_clearances === 0 ? "ok" : "bad") : ""}
          sub={ev ? `on ${ev.labelled_hits} practice labels · score ${ev.decision_score ?? "—"}` : "no practice labels"}
          title="True matches or review cases the engine cleared, measured on the labelled practice hits"
        />
        <ReproKpi r={s?.repro} onRun={onRepro} />
        <div className="tier-bar" aria-label="Tier distribution">
          <div className="tier-bar-track">
            {TIERS.map((t) => {
              const n = s?.tiers?.[t.id] ?? 0;
              return n ? <span key={t.id} className={`seg-fill t-${t.id}`} style={{ flexGrow: n }} title={`${t.label}: ${n}`} /> : null;
            })}
          </div>
          <div className="tier-bar-legend small">
            {TIERS.map((t) => (
              <span key={t.id} className={`t-${t.id}`}><span className="dot" />{t.short} {s?.tiers?.[t.id] ?? 0}</span>
            ))}
          </div>
        </div>
      </div>
    </header>
  );
}

const NO_FILTERS: Filters = { q: "", reason: "", flag: "", status: "" };

function isTyping(el: EventTarget | null): boolean {
  const t = el as HTMLElement | null;
  if (!t) return false;
  return t.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || !!t.closest(".xterm");
}

export default function App() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [tier, setTier] = useState<TierTab>("HUMAN_REVIEW");
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [selected, setSelected] = useState<string | null>(null);
  const [view, setView] = useState<View>("queue");
  const [running, setRunning] = useState(false);
  const [termOpen, setTermOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const searchRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(() => {
    Promise.all([api.summary(), api.decisions()])
      .then(([s, r]) => { setSummary(s); setRows(r); setErr(null); setVersion((v) => v + 1); })
      .catch((e) => setErr(`Cannot reach the API: ${e instanceof Error ? e.message : String(e)}`));
  }, []);
  useEffect(refresh, [refresh]);

  // Poll the repro badge while a check runs.
  const reproRunning = summary?.repro?.running;
  useEffect(() => {
    if (!reproRunning) return;
    const t = setInterval(() => {
      api.repro().then((r) => {
        setSummary((s) => (s ? { ...s, repro: r } : s));
      }).catch(() => undefined);
    }, 3000);
    return () => clearInterval(t);
  }, [reproRunning]);

  const startRepro = () => {
    api.startRepro().then((r) => setSummary((s) => (s ? { ...s, repro: r } : s))).catch((e) => setErr(String(e)));
  };

  const rerun = async () => {
    setRunning(true);
    try { await api.rerun(); refresh(); } catch (e) { setErr(String(e)); } finally { setRunning(false); }
  };

  const filtered = useMemo(() => {
    const ql = filters.q.trim().toLowerCase();
    return rows.filter((r) =>
      (!filters.reason || r.reason_code === filters.reason) &&
      (!filters.flag || r.flags.includes(filters.flag)) &&
      (!filters.status || r.status === filters.status) &&
      (!ql || [r.hit_id, r.alert_id, r.hit_name, r.customer_name, r.reason_code, r.customer_record_id, ...r.flags]
        .some((v) => v && v.toLowerCase().includes(ql))));
  }, [rows, filters]);

  const counts = useMemo(() => {
    const c: Record<string, number> = { ALL: filtered.length };
    for (const r of filtered) c[r.tier] = (c[r.tier] ?? 0) + 1;
    return c;
  }, [filtered]);

  const visible = useMemo(() => filtered.filter((r) => tier === "ALL" || r.tier === tier), [filtered, tier]);
  const reasons = useMemo(() => Array.from(new Set(rows.map((r) => r.reason_code))).sort(), [rows]);
  const flags = useMemo(() => Array.from(new Set(rows.flatMap((r) => r.flags))).sort(), [rows]);

  // Keyboard: j/k next/prev, 1-4 tiers, 0 all, / search, Esc leaves a field.
  const visibleRef = useRef(visible);
  visibleRef.current = visible;
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape" && isTyping(e.target) && !(e.target as HTMLElement).closest(".xterm")) {
        (e.target as HTMLElement).blur();
        return;
      }
      if (e.ctrlKey || e.metaKey || e.altKey || isTyping(e.target)) return;
      const tierKey = TIERS.find((t) => t.key === e.key);
      if (tierKey || e.key === "0") {
        setView("queue");
        setTier(tierKey ? tierKey.id : "ALL");
        e.preventDefault();
        return;
      }
      if (e.key === "/") {
        setView("queue");
        setTimeout(() => searchRef.current?.focus(), 0);
        e.preventDefault();
        return;
      }
      if (e.key === "j" || e.key === "k" || e.key === "ArrowDown" || e.key === "ArrowUp") {
        const list = visibleRef.current;
        if (!list.length) return;
        const i = list.findIndex((r) => r.hit_id === selectedRef.current);
        const down = e.key === "j" || e.key === "ArrowDown";
        const next = i < 0 ? 0 : Math.min(list.length - 1, Math.max(0, i + (down ? 1 : -1)));
        setView("queue");
        setSelected(list[next].hit_id);
        e.preventDefault();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const openHit = (id: string) => {
    setView("queue");
    const row = rows.find((r) => r.hit_id === id);
    if (row && tier !== "ALL" && row.tier !== tier) setTier(row.tier);
    setSelected(id);
  };

  return (
    <div className={`app ${termOpen ? "term-open" : ""}`}>
      <Header s={summary} view={view} setView={setView} onRerun={rerun} running={running} onRepro={startRepro} />
      {err && <div className="banner error">{err} <button className="link" onClick={refresh}>retry</button></div>}
      {!summary && !err && <div className="banner">Loading engine results… the server screens every hit on startup.</div>}
      <main className="main">
        {view === "queue" && (
          <div className="split">
            <Queue rows={visible} counts={counts} tier={tier} setTier={setTier} filters={filters} setFilters={setFilters}
              reasons={reasons} flags={flags} selected={selected} onSelect={setSelected} searchRef={searchRef} />
            <DetailPane id={selected} onReviewed={refresh} />
          </div>
        )}
        {view === "audit" && <Audit version={version} onOpen={openHit} />}
        {view === "policy" && <Policy s={summary} />}
      </main>
      <footer className="term">
        <button className="term-toggle" onClick={() => setTermOpen((o) => !o)} aria-expanded={termOpen}>
          {termOpen ? "▾" : "▸"} Terminal <span className="muted">screen summary | eval | explain &lt;HIT&gt; | repro | run — data defaults to the server's</span>
        </button>
        <div className="term-body" hidden={!termOpen}>
          <Terminal open={termOpen} selected={selected} />
        </div>
      </footer>
    </div>
  );
}
