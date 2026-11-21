import { useCallback, useEffect, useMemo, useState } from "react";
import { api, type Detail, type Person, type Rec, type TraceRow } from "./api";
import { DECISION_LABEL, FACET_LABEL, humanFlag, reasonLabel, TIER_BY_ID, whyTier } from "./labels";
import { Badge, EvidenceList, fmt, StatusPill, TierPill } from "./ui";

type Line = { v: string; src?: string; note?: string };

function vals(...xs: unknown[]): string[] {
  const out: string[] = [];
  for (const x of xs) {
    if (Array.isArray(x)) out.push(...x.map(String).filter(Boolean));
    else if (x !== null && x !== undefined && x !== "") out.push(String(x));
  }
  return out;
}

function latestKyc(p: Person | undefined): Rec | undefined {
  const approved = (p?.kyc ?? []).filter((k) => String(k.result ?? "").toLowerCase() === "approved");
  return approved.sort((a, b) => String(b.checked_at ?? "").localeCompare(String(a.checked_at ?? "")))[0];
}

interface CompareRow {
  label: string;
  facets: string[];
  customer: (rec: Rec, kyc: Rec | undefined, alert: Rec | null) => Line[];
  hit: (h: Rec) => Line[];
}

const COMPARE: CompareRow[] = [
  {
    label: "Name / aliases",
    facets: ["name"],
    customer: (r, k, a) => [
      ...vals(r.full_name).map((v) => ({ v, src: String(r.record_id ?? "") })),
      ...vals(r.preferred_name).map((v) => ({ v, note: "preferred", src: String(r.record_id ?? "") })),
      ...(k ? vals(k.verified_name).map((v) => ({ v, note: "KYC verified", src: String(k.check_id) })) : []),
      ...(!r.record_id ? vals(a?.customer_name).map((v) => ({ v, note: "screened" })) : []),
    ],
    hit: (h) => [...vals(h.name).map((v) => ({ v })), ...vals(h.aliases).map((v) => ({ v, note: "alias" }))],
  },
  {
    label: "Date of birth",
    facets: ["dob", "time"],
    customer: (r, k, a) => [
      ...vals(r.dob).map((v) => ({ v, note: "declared", src: String(r.record_id ?? "") })),
      ...(k ? vals(k.verified_dob).map((v) => ({ v, note: "KYC verified", src: String(k.check_id) })) : []),
      ...(!r.record_id ? vals(a?.dob).map((v) => ({ v, note: "screened" })) : []),
    ],
    hit: (h) => [
      ...vals(h.dobs).map((v) => ({ v })),
      ...vals(h.place_of_birth).map((v) => ({ v, note: "place of birth" })),
    ],
  },
  {
    label: "Countries",
    facets: ["location"],
    customer: (r, k, a) => [
      ...vals(r.nationality).map((v) => ({ v, note: "nationality", src: String(r.record_id ?? "") })),
      ...vals(r.residence_country).map((v) => ({ v, note: "residence", src: String(r.record_id ?? "") })),
      ...vals(r.city).map((v) => ({ v, note: "city", src: String(r.record_id ?? "") })),
      ...(k ? vals(k.document_country).map((v) => ({ v, note: "ID document", src: String(k.check_id) })) : []),
      ...(!r.record_id ? vals(a?.country, a?.city).map((v) => ({ v, note: "screened" })) : []),
    ],
    hit: (h) => [
      ...vals(h.nationality).map((v) => ({ v, note: "nationality" })),
      ...vals(h.citizenship).map((v) => ({ v, note: "citizenship" })),
      ...vals(h.countries).map((v) => ({ v, note: "country" })),
    ],
  },
  {
    label: "Entity type",
    facets: ["type"],
    customer: (r, _k, a) => vals(r.entity_type ?? a?.entity_type).map((v) => ({ v, src: r.record_id ? String(r.record_id) : undefined })),
    hit: (h) => vals(h.entity_type).map((v) => ({ v })),
  },
  {
    label: "Life status",
    facets: ["deceased"],
    customer: () => [],
    hit: (h) => [
      ...vals(h.life_status).map((v) => ({ v })),
      ...vals(h.deceased_date).map((v) => ({ v, note: "date of death" })),
    ],
  },
];

type Mark = { kind: "agree" | "contradict" | "inconclusive" | "unchecked"; rows: TraceRow[] };

function markFor(trace: TraceRow[], facets: string[]): Mark {
  const rows = trace.filter((t) => facets.includes(t.facet));
  if (rows.some((t) => t.signal === "contradiction")) return { kind: "contradict", rows };
  if (rows.some((t) => t.signal === "agreement")) return { kind: "agree", rows };
  if (rows.length) return { kind: "inconclusive", rows };
  return { kind: "unchecked", rows };
}

const MARK_TEXT: Record<Mark["kind"], string> = {
  agree: "= agrees",
  contradict: "≠ contradicts",
  inconclusive: "~ not decisive",
  unchecked: "? not checked",
};

function Cell({ lines }: { lines: Line[] }) {
  if (!lines.length) return <span className="muted">unknown</span>;
  return (
    <ul className="cell-lines">
      {lines.map((l, i) => (
        <li key={i}>
          {l.v}
          {(l.note || l.src) && (
            <span className="src">
              {l.note}
              {l.note && l.src ? " · " : ""}
              {l.src && <span className="mono">{l.src}</span>}
            </span>
          )}
        </li>
      ))}
    </ul>
  );
}

function Compare({ d, person }: { d: Detail; person: Person | undefined }) {
  const rec = person?.records[0] ?? {};
  const kyc = latestKyc(person);
  const trace = d.decision.trace;
  return (
    <table className="compare">
      <thead>
        <tr>
          <th className="facet-col"></th>
          <th>Customer{person ? <span className="muted"> · {person.record_ids.join(", ")}</span> : <span className="muted"> · screened details only</span>}</th>
          <th className="mark-col">Rule finding</th>
          <th>Watchlist hit<span className="muted"> · {d.decision.hit_id}</span></th>
        </tr>
      </thead>
      <tbody>
        {COMPARE.map((c) => {
          const m = markFor(trace, c.facets);
          const weak = m.rows.find((t) => t.weak);
          return (
            <tr key={c.label} className={`m-${m.kind}`}>
              <th className="facet-col">{c.label}</th>
              <td><Cell lines={c.customer(rec, kyc, d.alert)} /></td>
              <td className="mark-col">
                <span className={`mark mk-${m.kind}`}>{MARK_TEXT[m.kind]}</span>
                {weak && <span className="weak-tag" title={humanFlag(weak.weak_why ?? "")}>weak</span>}
                {Array.from(new Set(m.rows.filter((t) => t.signal !== "neutral" || m.kind === "inconclusive").map((t) => t.summary)))
                  .slice(0, 2).map((s) => <div key={s} className="mark-why">{s}</div>)}
              </td>
              <td><Cell lines={c.hit(d.hit)} /></td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

function Resolution({ d, person, setPerson }: { d: Detail; person: number; setPerson: (i: number) => void }) {
  const a = d.alert ?? {};
  const r = d.resolution;
  const links = ["account_ref", "login_handle", "email"].filter((k) => a[k]).map((k) => `${k.replace("_", " ")} ${a[k]}`);
  return (
    <ol className="steps">
      <li>
        <div className="step-title">Screened party <span className="mono muted">{d.decision.alert_id}</span></div>
        <div className="small">
          {fmt(a.customer_name)} · {fmt(a.entity_type)} · DOB {fmt(a.dob)} · {fmt(a.country)}{a.city ? `, ${a.city}` : ""}
          {" "}· screened {fmt(a.screened_at)}
        </div>
        <div className="small muted">Links on the alert: {links.length ? links.join(" · ") : "none"}</div>
      </li>
      {(r?.path ?? []).map((p, i) => (
        <li key={i}><div className="step-title">Link followed</div><div className="small mono">{p}</div></li>
      ))}
      <li className={r?.persons.length ? "" : "warn"}>
        <div className="step-title">
          {!r || r.method === "unlinked"
            ? "No link on the alert — decided on screened details only"
            : !r.persons.length
              ? "Links found no customer record"
              : r.persons.length === 1
                ? `Resolved to one customer via ${r.method.replace("_", " ")}`
                : `${r.persons.length} different people fit the alert (via ${r.method.replace("_", " ")})`}
        </div>
        {r && r.persons.length > 1 && (
          <div className="seg" role="tablist">
            {r.persons.map((p, i) => (
              <button key={p.key} role="tab" aria-selected={i === person} className={i === person ? "on" : ""} onClick={() => setPerson(i)}>
                {p.record_ids[0]}{p.record_ids.length > 1 ? ` +${p.record_ids.length - 1}` : ""}
              </button>
            ))}
          </div>
        )}
        {r?.persons.map((p, i) => (i === person || r.persons.length === 1) && (
          <div key={p.key} className="person">
            <div className="person-row"><span className="k">Records</span><EvidenceList ids={p.record_ids} /></div>
            <div className="person-row"><span className="k">KYC checks</span><EvidenceList ids={p.kyc_ids} empty="no identity check on file" /></div>
            <div className="person-row"><span className="k">Notes</span><EvidenceList ids={p.note_ids} empty="no support notes" /></div>
          </div>
        ))}
      </li>
    </ol>
  );
}

function Timeline({ d }: { d: Detail }) {
  const dec = d.decision;
  const decisive = new Set(dec.evidence);
  if (!dec.trace.length) return <p className="muted">No rule produced output for this hit.</p>;
  return (
    <ol className="timeline">
      {dec.trace.map((t, i) => {
        const isDecisive = !!t.reason && t.reason === dec.reason_code;
        return (
          <li key={i} className={`tl sig-${t.signal} ${isDecisive ? "decisive" : ""}`}>
            <span className="tl-dot" aria-hidden />
            <div className="tl-head">
              <span className="mono strong">{t.rule_id}</span>
              <span className="muted">{FACET_LABEL[t.facet] ?? t.facet}</span>
              <span className={`sig sig-${t.signal}`}>{t.signal}</span>
              {t.reason && <span className="muted small">→ {t.reason}</span>}
              {isDecisive && <span className="decisive-tag">decided the outcome</span>}
              {t.weak && <span className="weak-tag" title={humanFlag(t.weak_why ?? "")}>weak: {t.weak_why}</span>}
            </div>
            <div className="tl-body">{t.summary}</div>
            {t.evidence.length > 0 && <EvidenceList ids={t.evidence} highlight={decisive} />}
          </li>
        );
      })}
      <li className="tl sig-final">
        <span className="tl-dot" aria-hidden />
        <div className="tl-head">
          <span className="strong">Compiler</span>
          <span className="muted">fixed precedence: contradiction → agreement → review</span>
        </div>
        <div className="tl-body">
          <Badge decision={dec.decision} /> {dec.reason_code} → <TierPill tier={dec.tier} long />
        </div>
      </li>
    </ol>
  );
}

const ACTIONS: { id: string; label: string }[] = [
  { id: "SAME_ENTITY", label: "Same entity" },
  { id: "DIFFERENT_ENTITY", label: "Different entity (clear)" },
  { id: "REVIEW", label: "Keep in review" },
];

function readAnalyst(): string {
  try {
    return localStorage.getItem("analyst") ?? "";
  } catch {
    return "";
  }
}

function ActionPanel({ d, onDone }: { d: Detail; onDone: () => void }) {
  const [note, setNote] = useState("");
  const [analyst, setAnalyst] = useState(readAnalyst);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [ok, setOk] = useState<string | null>(null);
  const engine = d.decision.decision;
  const c = d.case;
  const me = analyst.trim().toLowerCase();
  const sameAnalyst = c.status === "AWAITING_SECOND" && c.analysts.includes(me);

  useEffect(() => { setNote(""); setErr(null); setOk(null); }, [d.decision.hit_id]);

  const submit = async (decision: string) => {
    if (!analyst.trim()) return setErr("Enter your analyst name — every action is attributed.");
    if (note.trim().length < 5) return setErr("A note is required for every action: say which facts you relied on.");
    setBusy(true);
    setErr(null);
    try {
      try { localStorage.setItem("analyst", analyst.trim()); } catch { /* private mode */ }
      const res = await api.review(d.decision.hit_id, { decision, note, analyst: analyst.trim() });
      setNote("");
      setOk(`Recorded: ${res.action} → ${DECISION_LABEL[res.decision] ?? res.decision} (${res.case.status === "AWAITING_SECOND" ? "awaiting second reviewer" : res.case.status.toLowerCase()})`);
      onDone();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="action-panel">
      <div className="ap-head">
        <h3>Analyst decision</h3>
        <span className="small muted">
          {c.status === "AWAITING_SECOND"
            ? `4-eyes: ${c.analysts.join(", ")} chose ${DECISION_LABEL[c.final_decision ?? ""] ?? c.final_decision}; a different analyst must confirm.`
            : d.decision.tier === "BORDERLINE"
              ? "4-eyes check: two different analysts must agree."
              : "Every action is logged with your name, time and note."}
        </span>
      </div>
      <div className="ap-row">
        <label className="field">
          <span>Analyst</span>
          <input value={analyst} onChange={(e) => setAnalyst(e.target.value)} placeholder="your name" />
        </label>
        <label className="field grow">
          <span>Note (required)</span>
          <textarea
            value={note}
            rows={2}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Which facts did you check? e.g. KYC DOB 1981-04-02 differs from the hit's 1956 DOB."
          />
        </label>
      </div>
      <div className="ap-row buttons">
        <button className="btn primary" disabled={busy} onClick={() => submit("CONFIRM")}>
          Accept engine: {DECISION_LABEL[engine] ?? engine}
        </button>
        <span className="muted small">or override to</span>
        {ACTIONS.filter((a) => a.id !== engine).map((a) => (
          <button key={a.id} className={`btn ${a.id === "DIFFERENT_ENTITY" ? "warn" : ""}`} disabled={busy} onClick={() => submit(a.id)}>
            {a.label}
          </button>
        ))}
      </div>
      {engine !== "DIFFERENT_ENTITY" && (
        <div className="small muted">Clearing a hit the engine did not clear needs a documented contradiction and a second analyst.</div>
      )}
      {sameAnalyst && <div className="small warn-text">You already reviewed this case; the second check must come from a different analyst.</div>}
      {err && <div className="error small">{err}</div>}
      {ok && <div className="ok small">{ok}</div>}
    </div>
  );
}

export default function DetailPane({ id, onReviewed }: { id: string | null; onReviewed: () => void }) {
  const [d, setD] = useState<Detail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [person, setPerson] = useState<number | null>(null);
  const load = useCallback(() => {
    if (!id) return;
    setErr(null);
    api.hit(id).then(setD).catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [id]);
  useEffect(load, [load]);
  useEffect(() => setPerson(null), [id]);

  const persons = d?.resolution?.persons ?? [];
  const chosenIdx = useMemo(() => {
    if (person !== null && person < persons.length) return person;
    const byId = persons.findIndex((p) => p.record_ids.includes(d?.decision.customer_record_id ?? ""));
    return byId >= 0 ? byId : 0;
  }, [d, persons, person]);
  const chosen: Person | undefined = persons[chosenIdx];

  if (!id)
    return (
      <section className="detail empty">
        <div className="empty-box">
          <strong>Select a hit to open its case file.</strong>
          <span className="muted">Use j / k to move through the queue, 1–4 to switch tiers.</span>
        </div>
      </section>
    );
  if (err) return <section className="detail"><div className="banner error">{err}</div></section>;
  if (!d || d.decision.hit_id !== id) return <section className="detail muted">Loading case file…</section>;
  const dec = d.decision;
  const label = d.practice_label;
  const tier = TIER_BY_ID[dec.tier];
  return (
    <section className="detail" aria-label={`Case file ${dec.hit_id}`}>
      <div className={`case-head t-${dec.tier}`}>
        <div className="case-title">
          <h2><span className="mono">{dec.hit_id}</span></h2>
          <TierPill tier={dec.tier} long />
          <StatusPill status={d.case.status} />
          {d.reviews.length > 0 && d.case.final_decision && (
            <span className="final-box">
              analyst: <Badge decision={d.case.final_decision} />
              {d.case.final_decision !== dec.decision && <strong> overrides engine</strong>}
            </span>
          )}
          {label && (
            <span className={`label-box ${label.decision === dec.decision ? "agree" : "differ"}`} title={fmt(label.reference_reason)}>
              practice label: {DECISION_LABEL[String(label.decision)] ?? String(label.decision)}
              {label.decision === dec.decision ? " · engine agrees" : " · engine differs"}
            </span>
          )}
        </div>
        <div className="why"><strong>Why this tier:</strong> {whyTier(dec)} <span className="muted">{tier?.who}.</span></div>
      </div>

      <div className="decision-card">
        <div className="dc-top">
          <Badge decision={dec.decision} />
          <span className="reason-code" title={dec.reason_code}>{reasonLabel(dec.reason_code)}</span>
          <span className="mono muted small">{dec.reason_code}</span>
          <span className="muted small">· customer <span className="mono">{dec.customer_record_id ?? "none resolved"}</span></span>
        </div>
        <p className="reason">{dec.reason}</p>
        <div className="dc-row">
          <span className="k">Deciding evidence</span>
          <EvidenceList ids={dec.evidence} empty="no record cited (nothing decisive)" />
        </div>
        {dec.flags.length > 0 && (
          <div className="dc-row">
            <span className="k">Weak-evidence flags</span>
            <div className="chips">{dec.flags.map((f) => <span key={f} className="chip flag" title={f}>{humanFlag(f)}</span>)}</div>
          </div>
        )}
      </div>

      <ActionPanel d={d} onDone={() => { load(); onReviewed(); }} />

      <div className="block">
        <h3>Customer vs watchlist hit</h3>
        <Compare d={d} person={chosen} />
        <div className="legend small muted">
          <span className="mark mk-agree">= agrees</span> supports a match ·
          <span className="mark mk-contradict">≠ contradicts</span> rules the hit out ·
          <span className="mark mk-inconclusive">~ not decisive</span> checked, settles nothing ·
          <span className="mark mk-unchecked">? not checked</span> no rule spoke — a missing fact is never a contradiction
        </div>
        {Boolean(d.hit.profile_notes || d.hit.details || d.hit.position_or_allegation) && (
          <details className="hit-more">
            <summary>Watchlist profile text</summary>
            {d.hit.position_or_allegation ? <p><strong>Position / allegation:</strong> {fmt(d.hit.position_or_allegation)}</p> : null}
            {d.hit.details ? <p>{fmt(d.hit.details)}</p> : null}
            {d.hit.profile_notes ? <p>{fmt(d.hit.profile_notes)}</p> : null}
          </details>
        )}
      </div>

      <div className="block">
        <h3>Resolution path — who is the customer?</h3>
        <Resolution d={d} person={chosenIdx} setPerson={setPerson} />
      </div>

      <div className="block">
        <h3>Rule trace</h3>
        <Timeline d={d} />
      </div>

      <div className="block">
        <h3>Case audit trail</h3>
        {d.reviews.length ? (
          <table className="grid">
            <thead><tr><th>#</th><th>When (UTC)</th><th>Analyst</th><th>Action</th><th>Decision</th><th>Note</th></tr></thead>
            <tbody>
              {d.reviews.map((r) => (
                <tr key={r.seq}>
                  <td className="mono">{r.seq}</td>
                  <td className="mono nowrap">{r.at.replace("T", " ").replace("+00:00", "")}</td>
                  <td>{r.analyst}</td>
                  <td className={r.action === "override" ? "strong" : ""}>{r.action}</td>
                  <td><Badge decision={r.decision} /></td>
                  <td>{r.note}</td>
                </tr>
              ))}
            </tbody>
          </table>
        ) : (
          <p className="muted small">No analyst action yet. Engine version and decision are reproducible from the same input.</p>
        )}
      </div>
    </section>
  );
}
