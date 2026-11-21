import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { api, ENGINE_FLAGS, type Health, type Overview as OverviewData, type ReproState, type Row, type Summary, type Tier } from "./api";
import Audit from "./Audit";
import DetailPane from "./Detail";
import { TIERS } from "./labels";
import Policy from "./Policy";
import Queue, { type Filters, type TierTab } from "./Queue";
import Overview from "./Overview";
import Terminal from "./Terminal";
import { TOUR, TourBar } from "./Tour";
import { SpotlightContext } from "./ui";

type View = "overview" | "queue" | "audit" | "policy";
type Theme = "light" | "dark";

function readPref(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}
function writePref(key: string, v: string) {
  try { localStorage.setItem(key, v); } catch { /* private mode */ }
}

/** Two interlocking rings: two engines, one decision. */
function Mark() {
  return (
    <svg className="brand-mark" viewBox="0 0 32 32" aria-hidden>
      <circle cx="12" cy="16" r="8.5" />
      <circle cx="20" cy="16" r="8.5" />
      <path d="M16 9.2a8.5 8.5 0 0 1 0 13.6a8.5 8.5 0 0 1 0-13.6z" className="bm-core" />
    </svg>
  );
}

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

function Header({ s, view, setView, onRerun, running, theme, setTheme, onTour, touring }: {
  s: Summary | null; view: View; setView: (v: View) => void; onRerun: () => void; running: boolean;
  theme: Theme; setTheme: (t: Theme) => void; onTour: () => void; touring: boolean;
}) {
  const open = (s?.case_status?.OPEN ?? 0) + (s?.case_status?.AWAITING_SECOND ?? 0);
  return (
    <header className="topbar">
      <div className="brand">
        <Mark />
        <div className="brand-text">
          <strong>Screening Review</strong>
          <span>sanctions &amp; PEP hits · dual-control engine</span>
        </div>
      </div>
      <nav className="nav" aria-label="Views">
        {([["overview", "Overview"], ["queue", "Review queue"], ["audit", "Audit trail"], ["policy", "Policy & controls"]] as const).map(([id, label]) => (
          <button key={id} className={view === id ? "on" : ""} aria-current={view === id ? "page" : undefined} onClick={() => setView(id)}>
            {label}
            {id === "queue" && s ? <span className="nav-count">{open}</span> : null}
          </button>
        ))}
      </nav>
      <div className="top-actions">
        <button className={`btn ${touring ? "" : "primary"}`} onClick={onTour} disabled={!s}>
          <svg viewBox="0 0 16 16" aria-hidden className="ico"><path d="M4 2.5v11l9-5.5z" /></svg>
          {touring ? "Restart demo" : "Start demo"}
        </button>
        <button className="btn icon" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          aria-label={theme === "dark" ? "Switch to light theme" : "Switch to presentation (dark) theme"}
          title={theme === "dark" ? "Light theme" : "Presentation theme (dark, projector friendly)"}>
          {theme === "dark"
            ? <svg viewBox="0 0 20 20" className="ico" aria-hidden><circle cx="10" cy="10" r="3.6" /><path d="M10 1.8v2M10 16.2v2M1.8 10h2M16.2 10h2M4.2 4.2l1.4 1.4M14.4 14.4l1.4 1.4M4.2 15.8l1.4-1.4M14.4 5.6l1.4-1.4" /></svg>
            : <svg viewBox="0 0 20 20" className="ico" aria-hidden><path d="M16.5 12.6A7 7 0 0 1 7.4 3.5a7 7 0 1 0 9.1 9.1z" /></svg>}
        </button>
        <button className="btn ghost small-btn" onClick={onRerun} disabled={running} title="Re-run both engines over every hit">
          {running ? "Running…" : "Re-run engine"}
        </button>
      </div>
    </header>
  );
}

/** Compact KPI strip above the queue and the other analyst views. */
function KpiStrip({ s, onRepro }: { s: Summary | null; onRepro: () => void }) {
  const ev = s?.eval;
  const auto = s?.automation;
  const eng = s?.engines;
  const open = (s?.case_status?.OPEN ?? 0) + (s?.case_status?.AWAITING_SECOND ?? 0);
  return (
    <div className="kpis">
      <Kpi label="Automation rate" value={pct(auto?.rate)}
        sub={auto ? `${auto.automated} automated · ${auto.needs_human} need a person` : ""}
        title="Share of hits decided on solid evidence with no person needed (auto-cleared + auto-escalated)" />
      <Kpi label="Open cases" value={open} sub={`${s?.case_status?.AWAITING_SECOND ?? 0} awaiting 2nd · ${s?.reviewed_hits ?? 0} reviewed`} />
      <Kpi
        label="Unsafe clearances"
        value={ev ? ev.unsafe_clearances : "n/a"}
        tone={ev ? (ev.unsafe_clearances === 0 ? "ok" : "bad") : ""}
        sub={ev ? `on ${ev.labelled_hits} labels · score ${ev.decision_score ?? "—"}` : "no practice labels"}
        title="True matches or review cases the engine cleared, measured on the labelled practice hits"
      />
      <Kpi label="Engine agreement" value={pct(eng?.agreement_rate)}
        tone={eng ? ((eng.agreement.CONFLICT ?? 0) > 0 ? "warn" : "") : ""}
        sub={eng ? `${eng.agreement.DISAGREE ?? 0} disagree · ${eng.agreement.CONFLICT ?? 0} conflict` : "dual control off"}
        title="Decision core and independent rule check reach the same decision on their own. Disagreements go to a 4-eyes check; a core clearance the check contradicts is never cleared." />
      <Kpi label="Escalated by policy" value={eng ? eng.escalated_by_policy : "—"}
        sub={eng ? `${eng.with_reviewer_action} with a reviewer action` : ""}
        title="Hits the decision core kept in review under a Head of Financial Crime policy call instead of clearing them" />
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
  );
}

const NO_FILTERS: Filters = { q: "", reason: "", flag: "", status: "", engine: "" };

/** Shown until /api/health reports ready: both engines screen every hit on startup (~70 s on dev). */
function Loading({ h, err }: { h: Health | null; err: string | null }) {
  const p = h?.progress;
  const share = p && p.total ? Math.min(1, p.done / p.total) : null;
  return (
    <div className="loading-screen" role="status" aria-live="polite">
      <div className="loading-card">
        <Mark />
        <strong>Starting the screening engines…</strong>
        <p className="muted small">
          The server runs two independent engines over every hit before it answers: the <b>decision core</b> decides,
          an <b>independent rule check</b> runs alongside. This takes about a minute on the dev data.
        </p>
        {h?.status === "error" ? (
          <div className="error small">Engine failed to start: {h.error}</div>
        ) : (
          <>
            <div className="progress"><span style={{ width: share === null ? "8%" : `${Math.max(4, share * 100)}%` }} className={share === null ? "indet" : ""} /></div>
            <div className="small muted">
              {h ? <>{h.stage ?? "starting"}{p && p.total ? ` · ${p.done}/${p.total} hits` : ""} · {Math.round(h.elapsed_seconds)}s elapsed</> : "waiting for the API…"}
            </div>
          </>
        )}
        {err && <div className="small warn-text">{err}</div>}
      </div>
    </div>
  );
}

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
  const [view, setView] = useState<View>("overview");
  const [overview, setOverview] = useState<OverviewData | null>(null);
  const [tourStep, setTourStep] = useState<number | null>(null);
  const [theme, setThemeState] = useState<Theme>(() => (readPref("theme") === "dark" ? "dark" : "light"));
  const setTheme = (t: Theme) => { setThemeState(t); writePref("theme", t); };
  useEffect(() => { document.documentElement.dataset.theme = theme; }, [theme]);
  const [termH, setTermH] = useState(() => Math.max(160, Number(readPref("termH")) || 320));
  const [running, setRunning] = useState(false);
  const [termOpen, setTermOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [version, setVersion] = useState(0);
  const searchRef = useRef<HTMLInputElement>(null);

  const [health, setHealth] = useState<Health | null>(null);
  const [healthErr, setHealthErr] = useState<string | null>(null);
  const ready = !!health?.ready;

  const refresh = useCallback(() => {
    Promise.all([api.summary(), api.decisions(), api.overview().catch(() => null)])
      .then(([s, r, o]) => { setSummary(s); setRows(r); setOverview(o); setErr(null); setVersion((v) => v + 1); })
      .catch((e) => setErr(`Cannot reach the API: ${e instanceof Error ? e.message : String(e)}`));
  }, []);

  // Poll /api/health until both engines have screened every hit, then load the results.
  useEffect(() => {
    if (ready) return;
    let stop = false;
    const tick = () => {
      api.health()
        .then((h) => { if (!stop) { setHealth(h); setHealthErr(null); } })
        .catch((e) => { if (!stop) setHealthErr(`API not reachable yet (${e instanceof Error ? e.message : String(e)}) — retrying`); });
    };
    tick();
    const t = setInterval(tick, 1500);
    return () => { stop = true; clearInterval(t); };
  }, [ready]);
  useEffect(() => { if (ready) refresh(); }, [ready, refresh]);

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
      (!filters.engine || r.flags.includes(filters.engine)) &&
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
  const engineCounts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const f of ENGINE_FLAGS) c[f] = rows.filter((r) => r.flags.includes(f)).length;
    return c;
  }, [rows]);

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

  const openTier = (t: Tier) => { setFilters(NO_FILTERS); setTier(t); setView("queue"); setSelected(null); };
  const openFiltered = (t: Tier, f: { reason?: string; engine?: string; flag?: string }) => {
    setFilters({ ...NO_FILTERS, reason: f.reason ?? "", engine: f.engine ?? "", flag: f.flag ?? "" });
    setTier(t);
    setView("queue");
    setSelected(null);
  };

  // Guided demo: each step opens a showcase hit's case file.
  const goStep = useCallback((n: number) => {
    const st = TOUR[Math.max(0, Math.min(TOUR.length - 1, n))];
    setTourStep(TOUR.indexOf(st));
    setFilters(NO_FILTERS);
    setTier(st.tier);
    setView("queue");
    setSelected(st.hit);
  }, []);
  const exitTour = useCallback(() => { setTourStep(null); }, []);
  const step = tourStep === null ? null : TOUR[tourStep];
  const spotlight = useMemo(() => new Set(step?.spotlight ?? []), [step]);

  // Resizable terminal dock: drag the handle; xterm refits on window resize.
  const startDrag = (e: { preventDefault: () => void; clientY: number }) => {
    e.preventDefault();
    const y0 = e.clientY, h0 = termH;
    let h = h0;
    const move = (ev: PointerEvent) => {
      h = Math.min(Math.round(window.innerHeight * 0.75), Math.max(140, h0 + (y0 - ev.clientY)));
      setTermH(h);
      window.dispatchEvent(new Event("resize"));
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      writePref("termH", String(h));
      window.dispatchEvent(new Event("resize"));
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  };

  return (
    <SpotlightContext.Provider value={spotlight}>
    <div className={`app ${termOpen ? "term-open" : ""} ${step ? "touring" : ""}`}>
      <Header s={summary} view={view} setView={(v) => { setView(v); if (v !== "queue") exitTour(); }} onRerun={rerun}
        running={running || !ready} theme={theme} setTheme={setTheme} onTour={() => goStep(0)} touring={!!step} />
      {err && <div className="banner error">{err} <button className="link" onClick={refresh}>retry</button></div>}
      {ready && !summary && !err && <div className="banner">Loading engine results…</div>}
      {!ready && <Loading h={health} err={healthErr} />}
      <main className="main" hidden={!ready}>
        {view === "overview" && (
          <Overview s={summary} o={overview} rows={rows} onTier={openTier} onFilter={openFiltered}
            onStartTour={() => goStep(0)} onRepro={startRepro} />
        )}
        {view !== "overview" && !step && <KpiStrip s={summary} onRepro={startRepro} />}
        {view === "queue" && (
          <div className="split">
            <Queue rows={visible} counts={counts} engineCounts={engineCounts} tier={tier} setTier={setTier} filters={filters} setFilters={setFilters}
              reasons={reasons} flags={flags} selected={selected} onSelect={setSelected} searchRef={searchRef} />
            <div className="detail-wrap">
              <DetailPane id={selected} onReviewed={refresh} focus={step && step.hit === selected ? step.focus : null} />
              {step && tourStep !== null && <TourBar step={tourStep} setStep={goStep} onExit={exitTour} onFinish={() => { exitTour(); setView("overview"); }} />}
            </div>
          </div>
        )}
        {view === "audit" && <Audit version={version} onOpen={openHit} />}
        {view === "policy" && <Policy s={summary} />}
      </main>
      <footer className="term" style={{ ["--term-h" as string]: `${termH}px` }}>
        {termOpen && <div className="term-grip" onPointerDown={startDrag} role="separator" aria-orientation="horizontal" aria-label="Resize the live CLI panel" title="Drag to resize" />}
        <button className="term-toggle" onClick={() => setTermOpen((o) => !o)} aria-expanded={termOpen}>
          <svg viewBox="0 0 16 16" className={`ico chev ${termOpen ? "up" : ""}`} aria-hidden><path d="m4 10 4-4 4 4" /></svg>
          <span className="term-title">Live CLI</span>
          <span className="term-live" aria-hidden />
          <span className="term-hint">screen summary · eval · explain &lt;HIT&gt; · diff · queue · policy · repro · run</span>
        </button>
        <div className="term-body" hidden={!termOpen}>
          <Terminal open={termOpen} selected={selected} />
        </div>
      </footer>
    </div>
    </SpotlightContext.Provider>
  );
}
