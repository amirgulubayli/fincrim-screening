import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Detail, type Row, type Summary, type Tier } from "./api";
import Terminal from "./Terminal";

const TIERS: { id: Tier | "ALL"; label: string }[] = [
  { id: "HUMAN_REVIEW", label: "Human review" },
  { id: "BORDERLINE", label: "Borderline" },
  { id: "AUTO_ESCALATE", label: "Auto-escalated" },
  { id: "AUTO_CLEAR", label: "Auto-cleared" },
  { id: "ALL", label: "All" },
];

const DECISION_LABEL: Record<string, string> = {
  SAME_ENTITY: "Same entity",
  DIFFERENT_ENTITY: "Different entity",
  REVIEW: "Review",
};

function Badge({ decision }: { decision: string }) {
  return <span className={`badge badge-${decision}`}>{DECISION_LABEL[decision] ?? decision}</span>;
}

function fmt(v: unknown): string {
  if (v === null || v === undefined || v === "") return "-";
  if (Array.isArray(v)) return v.length ? v.join("; ") : "-";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function Kpis({ s, onRerun, running }: { s: Summary | null; onRerun: () => void; running: boolean }) {
  const t = s?.tiers ?? {};
  const unsafe = s?.eval?.unsafe_clearances;
  return (
    <header className="topbar">
      <div className="brand">
        <strong>Screening review</strong>
        <span className="muted">
          engine {s?.engine_version ?? "-"} · {s?.data ?? ""} · run {s?.run_seconds ?? "-"}s
        </span>
      </div>
      <div className="kpis">
        <Kpi label="Hits" value={s?.hits} />
        <Kpi label="Auto-cleared" value={t.AUTO_CLEAR ?? 0} />
        <Kpi label="Auto-escalated" value={t.AUTO_ESCALATE ?? 0} />
        <Kpi label="Borderline" value={t.BORDERLINE ?? 0} />
        <Kpi label="Human review" value={t.HUMAN_REVIEW ?? 0} />
        <Kpi
          label="Unsafe clearances"
          value={unsafe === undefined ? "n/a" : unsafe}
          tone={unsafe === undefined ? "" : unsafe === 0 ? "ok" : "bad"}
          title={s?.eval ? `vs ${s.eval.labelled_hits} practice labels, decision score ${s.eval.decision_score}` : "no practice labels"}
        />
        <Kpi label="Reviewed" value={s?.reviewed_hits ?? 0} />
      </div>
      <button className="btn" onClick={onRerun} disabled={running}>
        {running ? "Running..." : "Re-run engine"}
      </button>
    </header>
  );
}

function Kpi({ label, value, tone = "", title }: { label: string; value: unknown; tone?: string; title?: string }) {
  return (
    <div className={`kpi ${tone}`} title={title}>
      <div className="kpi-value">{fmt(value)}</div>
      <div className="kpi-label">{label}</div>
    </div>
  );
}

function Queue({ rows, tier, setTier, q, setQ, selected, onSelect }: {
  rows: Row[]; tier: Tier | "ALL"; setTier: (t: Tier | "ALL") => void;
  q: string; setQ: (s: string) => void; selected: string | null; onSelect: (id: string) => void;
}) {
  const counts = useMemo(() => {
    const c: Record<string, number> = { ALL: rows.length };
    for (const r of rows) c[r.tier] = (c[r.tier] ?? 0) + 1;
    return c;
  }, [rows]);
  const visible = useMemo(() => {
    const ql = q.trim().toLowerCase();
    return rows.filter((r) => (tier === "ALL" || r.tier === tier) &&
      (!ql || [r.hit_id, r.alert_id, r.hit_name, r.customer_name, r.reason_code, r.customer_record_id]
        .some((v) => v && v.toLowerCase().includes(ql))));
  }, [rows, tier, q]);
  return (
    <aside className="queue">
      <div className="tabs" role="tablist">
        {TIERS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tier === t.id}
            className={`tab ${tier === t.id ? "active" : ""}`} onClick={() => setTier(t.id)}>
            {t.label} <span className="count">{counts[t.id] ?? 0}</span>
          </button>
        ))}
      </div>
      <input className="search" placeholder="Search hit, alert, name, reason..." value={q}
        onChange={(e) => setQ(e.target.value)} />
      <ul className="list">
        {visible.map((r) => (
          <li key={r.hit_id} className={selected === r.hit_id ? "sel" : ""} onClick={() => onSelect(r.hit_id)}>
            <div className="row1">
              <span className="mono">{r.hit_id}</span>
              <Badge decision={r.decision} />
            </div>
            <div className="row2">
              <span>{r.customer_name ?? "-"}</span>
              <span className="muted"> vs {r.hit_name ?? "-"}</span>
            </div>
            <div className="row3 muted">
              {r.reason_code}
              {r.review && <span className="reviewed">reviewed: {r.review}</span>}
            </div>
          </li>
        ))}
        {!visible.length && <li className="empty muted">No hits in this queue.</li>}
      </ul>
    </aside>
  );
}

const FACTS: { label: string; alert?: string; customer?: string[]; hit: string }[] = [
  { label: "Name", alert: "customer_name", customer: ["full_name", "preferred_name"], hit: "name" },
  { label: "Aliases", hit: "aliases" },
  { label: "Entity type", alert: "entity_type", customer: ["entity_type"], hit: "entity_type" },
  { label: "DOB / inc. date", alert: "dob", customer: ["dob"], hit: "dobs" },
  { label: "Nationality", customer: ["nationality"], hit: "nationality" },
  { label: "Country", alert: "country", customer: ["residence_country"], hit: "countries" },
  { label: "City / address", alert: "city", customer: ["city", "address"], hit: "addresses" },
  { label: "Occupation", customer: ["occupation", "employer"], hit: "position_or_allegation" },
  { label: "Identifiers", customer: ["registration_number"], hit: "identifiers" },
  { label: "Life status", hit: "life_status" },
  { label: "Category", hit: "primary_category" },
];

function Facts({ d }: { d: Detail }) {
  const person = d.resolution?.persons.find((p) => p.key === d.decision.customer_record_id) ?? d.resolution?.persons[0];
  const rec = person?.records[0] ?? {};
  const kyc = person?.kyc[0];
  return (
    <table className="facts">
      <thead>
        <tr><th></th><th>Screened (alert)</th><th>Customer {person ? `(${person.record_ids.join(", ")})` : ""}</th><th>Watchlist hit</th></tr>
      </thead>
      <tbody>
        {FACTS.map((f) => (
          <tr key={f.label}>
            <th>{f.label}</th>
            <td>{f.alert ? fmt(d.alert?.[f.alert]) : ""}</td>
            <td>{f.customer ? f.customer.map((k) => rec[k]).filter((v) => v !== null && v !== undefined && v !== "").map(fmt).join(" · ") || "-" : ""}</td>
            <td>{fmt(d.hit[f.hit])}</td>
          </tr>
        ))}
        {kyc && (
          <tr>
            <th>Latest KYC</th>
            <td></td>
            <td colSpan={2}>
              <span className="mono">{fmt(kyc.check_id)}</span> {fmt(kyc.verified_name)}, DOB {fmt(kyc.verified_dob)},{" "}
              {fmt(kyc.document_type)} {fmt(kyc.document_country)} ({fmt(kyc.checked_at)})
            </td>
          </tr>
        )}
      </tbody>
    </table>
  );
}

function ActionBar({ d, onDone }: { d: Detail; onDone: () => void }) {
  const [note, setNote] = useState("");
  const [analyst, setAnalyst] = useState(() => localStorage.getItem("analyst") ?? "analyst");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async (decision: string) => {
    if (decision !== "CONFIRM" && !note.trim()) {
      setErr("An override needs a note explaining why.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      localStorage.setItem("analyst", analyst);
      await api.review(d.decision.hit_id, { decision, note, analyst });
      setNote("");
      onDone();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="actions">
      <input className="analyst" value={analyst} onChange={(e) => setAnalyst(e.target.value)} aria-label="Analyst" />
      <input className="note" placeholder="Note (required for an override)" value={note}
        onChange={(e) => setNote(e.target.value)} />
      <button className="btn primary" disabled={busy} onClick={() => submit("CONFIRM")}>Confirm</button>
      <span className="muted">Override:</span>
      {["SAME_ENTITY", "DIFFERENT_ENTITY", "REVIEW"].map((x) => (
        <button key={x} className="btn" disabled={busy || x === d.decision.decision} onClick={() => submit(x)}>
          {x === "SAME_ENTITY" ? "Same" : x === "DIFFERENT_ENTITY" ? "Different" : "Review"}
        </button>
      ))}
      {err && <div className="error">{err}</div>}
    </div>
  );
}

function DetailPane({ id, onReviewed }: { id: string | null; onReviewed: () => void }) {
  const [d, setD] = useState<Detail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const load = useCallback(() => {
    if (!id) return;
    setErr(null);
    api.hit(id).then(setD).catch((e) => setErr(String(e)));
  }, [id]);
  useEffect(load, [load]);
  if (!id) return <section className="detail empty muted">Select a hit from the queue.</section>;
  if (err) return <section className="detail error">{err}</section>;
  if (!d || d.decision.hit_id !== id) return <section className="detail muted">Loading...</section>;
  const dec = d.decision;
  const label = d.practice_label;
  return (
    <section className="detail">
      <div className="detail-head">
        <div>
          <h2>
            <span className="mono">{dec.hit_id}</span> <Badge decision={dec.decision} />
            <span className={`tier tier-${dec.tier}`}>{dec.tier.replace("_", " ")}</span>
          </h2>
          <div className="muted">
            alert <span className="mono">{dec.alert_id}</span> · reason code <strong>{dec.reason_code}</strong> ·
            customer <span className="mono">{dec.customer_record_id ?? "none"}</span>
            {d.resolution && <> · resolved via {d.resolution.method}</>}
          </div>
        </div>
        {label && (
          <div className="label-box" title={fmt(label.reference_reason)}>
            practice label: <Badge decision={String(label.decision)} />
          </div>
        )}
      </div>

      <p className="reason">{dec.reason}</p>
      <div className="chips">
        {dec.evidence.map((e) => <span key={e} className="chip mono">{e}</span>)}
        {dec.flags.map((f) => <span key={f} className="chip flag">{f}</span>)}
      </div>

      <ActionBar d={d} onDone={() => { load(); onReviewed(); }} />

      {d.reviews.length > 0 && (
        <div className="block">
          <h3>Analyst audit trail</h3>
          <table className="grid">
            <thead><tr><th>When</th><th>Analyst</th><th>Action</th><th>Decision</th><th>Note</th></tr></thead>
            <tbody>
              {d.reviews.map((r, i) => (
                <tr key={i}><td className="mono">{r.at}</td><td>{r.analyst}</td><td>{r.action}</td>
                  <td><Badge decision={r.decision} /></td><td>{r.note}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="block">
        <h3>Customer vs hit</h3>
        <Facts d={d} />
        {d.resolution && (
          <div className="muted small">
            {d.resolution.path.join(" → ")}
            {d.resolution.persons.map((p) => (
              <div key={p.key}>
                {p.record_ids.join(", ")} · KYC {p.kyc_ids.join(", ") || "-"} · notes {p.note_ids.join(", ") || "-"}
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="block">
        <h3>Rule trace</h3>
        <table className="grid trace">
          <thead><tr><th>Rule</th><th>Facet</th><th>Signal</th><th>Summary</th><th>Evidence</th></tr></thead>
          <tbody>
            {dec.trace.map((t, i) => (
              <tr key={i} className={t.weak ? "weak" : ""}>
                <td className="mono">{t.rule_id}</td>
                <td>{t.facet}</td>
                <td><span className={`sig sig-${t.signal}`}>{t.signal}</span></td>
                <td>{t.summary}{t.weak && <div className="weak-why">weak: {t.weak_why}</div>}</td>
                <td className="mono small">{t.evidence.join(", ")}</td>
              </tr>
            ))}
            {!dec.trace.length && <tr><td colSpan={5} className="muted">No rule output.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  );
}

export default function App() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [rows, setRows] = useState<Row[]>([]);
  const [tier, setTier] = useState<Tier | "ALL">("HUMAN_REVIEW");
  const [q, setQ] = useState("");
  const [selected, setSelected] = useState<string | null>(null);
  const [running, setRunning] = useState(false);
  const [termOpen, setTermOpen] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const refresh = useCallback(() => {
    Promise.all([api.summary(), api.decisions()])
      .then(([s, r]) => { setSummary(s); setRows(r); setErr(null); })
      .catch((e) => setErr(String(e)));
  }, []);
  useEffect(refresh, [refresh]);

  const rerun = async () => {
    setRunning(true);
    try { await api.rerun(); refresh(); } catch (e) { setErr(String(e)); } finally { setRunning(false); }
  };

  return (
    <div className={`app ${termOpen ? "term-open" : ""}`}>
      <Kpis s={summary} onRerun={rerun} running={running} />
      {err && <div className="banner error">{err}</div>}
      <main className="main">
        <Queue rows={rows} tier={tier} setTier={setTier} q={q} setQ={setQ} selected={selected} onSelect={setSelected} />
        <DetailPane id={selected} onReviewed={refresh} />
      </main>
      <footer className="term">
        <button className="term-toggle" onClick={() => setTermOpen((o) => !o)}>
          {termOpen ? "▾" : "▸"} Terminal <span className="muted">screen run | explain | eval | repro | summary</span>
        </button>
        <div className="term-body" hidden={!termOpen}>
          <Terminal open={termOpen} />
        </div>
      </footer>
    </div>
  );
}
