import { useEffect, useMemo, useState, type ReactNode } from "react";
import type { Overview as OverviewData, ReproState, Row, Summary, Tier } from "./api";
import { humanFlag, TIER_BY_ID } from "./labels";

/** Visual order of the flow: automation on top, people below. */
const FLOW: Tier[] = ["AUTO_CLEAR", "AUTO_ESCALATE", "BORDERLINE", "HUMAN_REVIEW"];

const FLOW_NOTE: Record<Tier, string> = {
  AUTO_CLEAR: "hard contradiction, both engines agree",
  AUTO_ESCALATE: "confirmed match, straight to compliance",
  BORDERLINE: "decided, a second analyst confirms",
  HUMAN_REVIEW: "the SOP says a person decides",
};

export function reducedMotion(): boolean {
  return typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
}

/** Animated count-up; jumps straight to the value when the user prefers reduced motion. */
function useCountUp(target: number | null | undefined, ms = 1400, delay = 0): number {
  const [v, setV] = useState(0);
  useEffect(() => {
    if (target === null || target === undefined) return;
    if (reducedMotion()) { setV(target); return; }
    let raf = 0;
    let start = 0;
    const t0 = setTimeout(() => {
      const step = (now: number) => {
        if (!start) start = now;
        const p = Math.min(1, (now - start) / ms);
        const e = 1 - Math.pow(1 - p, 4);
        setV(target * e);
        if (p < 1) raf = requestAnimationFrame(step);
      };
      raf = requestAnimationFrame(step);
    }, delay);
    return () => { clearTimeout(t0); cancelAnimationFrame(raf); };
  }, [target, ms, delay]);
  return v;
}

function Num({ value, decimals = 0, suffix = "", prefix = "", delay = 0 }: {
  value: number | null | undefined; decimals?: number; suffix?: string; prefix?: string; delay?: number;
}) {
  const v = useCountUp(value, 1500, delay);
  if (value === null || value === undefined) return <span className="num">—</span>;
  return (
    <span className="num" aria-label={`${prefix}${value.toFixed(decimals)}${suffix}`}>
      {prefix}{v.toFixed(decimals)}<span className="num-suffix">{suffix}</span>
    </span>
  );
}

function Stat({ label, children, sub, tone = "", i = 0 }: { label: string; children: ReactNode; sub?: ReactNode; tone?: string; i?: number }) {
  return (
    <div className={`stat ${tone}`} style={{ ["--i" as string]: i }}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{children}</div>
      {sub && <div className="stat-sub">{sub}</div>}
    </div>
  );
}

/* ------------------------------------------------------------------ flow (Sankey) */

function FlowVisual({ tiers, total, onTier }: { tiers: Record<string, number>; total: number; onTier: (t: Tier) => void }) {
  const W = 1000, H = 380, top = 24, bottom = H - 24;
  const span = bottom - top;
  const gap = 16;
  const srcX = 40, srcW = 18, dstX = 640, dstW = 18;
  const counts = FLOW.map((t) => tiers[t] ?? 0);
  const sum = counts.reduce((a, b) => a + b, 0) || 1;
  const dstSpan = span - gap * (FLOW.length - 1);
  let sy = top;
  let dy = top;
  const links = FLOW.map((t, i) => {
    const hS = (counts[i] / sum) * span;
    const hD = Math.max(2, (counts[i] / sum) * dstSpan);
    const l = { t, n: counts[i], s0: sy, s1: sy + hS, d0: dy, d1: dy + hD };
    sy += hS;
    dy += hD + gap;
    return l;
  });
  // label centres: keep at least 50px apart so thin tiers stay readable
  const ly: number[] = [];
  links.forEach((l, i) => { ly.push(Math.max((l.d0 + l.d1) / 2, i ? ly[i - 1] + 50 : top + 18)); });
  const over = ly[ly.length - 1] - (bottom - 18);
  if (over > 0) for (let i = ly.length - 1; i >= 0; i--) { ly[i] -= over; if (i && ly[i - 1] <= ly[i] - 50) break; }
  for (let i = ly.length - 2; i >= 0; i--) ly[i] = Math.min(ly[i], ly[i + 1] - 50);
  const x0 = srcX + srcW, x1 = dstX, cx = (x0 + x1) / 2;
  const autoN = (tiers.AUTO_CLEAR ?? 0) + (tiers.AUTO_ESCALATE ?? 0);
  const humanN = (tiers.BORDERLINE ?? 0) + (tiers.HUMAN_REVIEW ?? 0);
  const autoBot = links[1].d1, humanTop = links[2].d0;
  return (
    <svg className="flow" viewBox={`0 0 ${W} ${H}`} role="img"
      aria-label={`${total} hits split into ${FLOW.map((t) => `${TIER_BY_ID[t].short} ${tiers[t] ?? 0}`).join(", ")}`}>
      <defs>
        {links.map((l) => (
          <linearGradient key={l.t} id={`g-${l.t}`} x1="0" x2="1" y1="0" y2="0">
            <stop offset="0%" stopColor="var(--flow-src)" stopOpacity=".55" />
            <stop offset="70%" stopColor={`var(--t-${l.t})`} stopOpacity=".55" />
            <stop offset="100%" stopColor={`var(--t-${l.t})`} stopOpacity=".8" />
          </linearGradient>
        ))}
      </defs>
      {links.map((l, i) => (
        <path key={l.t} className="flow-link" style={{ ["--i" as string]: i }}
          d={`M${x0},${l.s0} C${cx},${l.s0} ${cx},${l.d0} ${x1},${l.d0} L${x1},${l.d1} C${cx},${l.d1} ${cx},${l.s1} ${x0},${l.s1} Z`}
          fill={`url(#g-${l.t})`} onClick={() => onTier(l.t)}>
          <title>{`${TIER_BY_ID[l.t].short}: ${l.n} hits`}</title>
        </path>
      ))}
      {/* the two-engine gate */}
      <g className="flow-gate">
        <line x1={cx} x2={cx} y1={top - 10} y2={bottom + 10} />
        <rect x={cx - 74} y={H / 2 - 30} width={148} height={60} rx={10} />
        <text x={cx} y={H / 2 - 7} textAnchor="middle" className="gate-title">2 engines</text>
        <text x={cx} y={H / 2 + 14} textAnchor="middle" className="gate-sub">core + independent check</text>
      </g>
      <rect className="flow-src" x={srcX} y={top} width={srcW} height={span} rx={3} />
      {links.map((l, i) => (
        <g key={l.t} className={`flow-node t-${l.t}`} style={{ ["--i" as string]: i }} onClick={() => onTier(l.t)} tabIndex={0}
          role="button" aria-label={`Open ${TIER_BY_ID[l.t].short} queue (${l.n} hits)`}
          onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); onTier(l.t); } }}>
          <rect x={dstX} y={l.d0} width={dstW} height={l.d1 - l.d0} rx={3} fill={`var(--t-${l.t})`} />
          {Math.abs(ly[i] - (l.d0 + l.d1) / 2) > 2 && (
            <path className="node-leader" d={`M${dstX + dstW + 2},${(l.d0 + l.d1) / 2} L${dstX + 28},${ly[i] - 10}`} />
          )}
          <text x={dstX + 34} y={ly[i] - 4}>
            <tspan className="node-n">{l.n}</tspan>
            <tspan className="node-label" dx={10}>{TIER_BY_ID[l.t].short}</tspan>
          </text>
          <text x={dstX + 34} y={ly[i] + 16} className="node-note">{FLOW_NOTE[l.t]} · {((l.n / (total || 1)) * 100).toFixed(1)}%</text>
        </g>
      ))}
      {/* brackets: automated vs people */}
      <g className="flow-bracket auto">
        <path d={`M${W - 18},${top + 2} h8 V${autoBot - 2} h-8`} />
        <text x={W - 4} y={(top + autoBot) / 2} transform={`rotate(90 ${W - 4} ${(top + autoBot) / 2})`} textAnchor="middle">
          automated · {autoN}
        </text>
      </g>
      <g className="flow-bracket human">
        <path d={`M${W - 18},${humanTop + 2} h8 V${bottom - 2} h-8`} />
        <text x={W - 4} y={(humanTop + bottom) / 2} transform={`rotate(90 ${W - 4} ${(humanTop + bottom) / 2})`} textAnchor="middle">
          a person · {humanN}
        </text>
      </g>
      <text x={srcX - 2} y={top - 8} className="src-label">{total} hits</text>
    </svg>
  );
}

/* ------------------------------------------------------------------ why humans still review */

const REASON_TEXT: Record<string, { title: string; body: string }> = {
  SOP_NO_DOB: {
    title: "SOP requires a person: no usable hit DOB",
    body: "Name and country agree, but the listing carries no date of birth to confirm or rule out the match. The SOP forbids clearing on a missing fact.",
  },
  HOFC_POLICY: {
    title: "Head of Financial Crime caution",
    body: "A literal contradiction the policy will not trust on its own (e.g. a former single state). Escalated, not cleared, with a Reviewer instruction.",
  },
  AMBIGUOUS_CUSTOMER: {
    title: "Ambiguous customer",
    body: "Several different customers fit the alert and they lead to different outcomes, so no single answer is safe.",
  },
  NO_CUSTOMER_RECORD: { title: "No customer record", body: "The alert's links led to no customer record." },
  ENGINES_CONFLICT: { title: "Engines conflict", body: "The core would clear but the independent check confirms a match: never cleared." },
  OTHER: { title: "Other insufficient data", body: "Nothing settles it either way." },
};

function Breakdown({ reasons, rows, onFilter }: {
  reasons: Record<string, number>; rows: Row[];
  onFilter: (tier: Tier, f: { reason?: string; engine?: string; flag?: string }) => void;
}) {
  const reviewTotal = Object.values(reasons).reduce((a, b) => a + b, 0) || 1;
  const border = rows.filter((r) => r.tier === "BORDERLINE");
  const disagree = border.filter((r) => r.flags.includes("ENGINES_DISAGREE")).length;
  const weak = new Map<string, number>();
  for (const r of border) {
    if (r.flags.includes("ENGINES_DISAGREE")) continue;
    const f = r.flags.find((x) => x !== "ESCALATED_BY_POLICY") ?? "OTHER";
    weak.set(f, (weak.get(f) ?? 0) + 1);
  }
  const weakTop = Array.from(weak.entries()).sort((a, b) => b[1] - a[1]);
  const max = Math.max(1, ...Object.values(reasons));
  const filterFor = (k: string) =>
    k === "HOFC_POLICY" ? { engine: "ESCALATED_BY_POLICY" } : k === "AMBIGUOUS_CUSTOMER" ? { reason: "AMBIGUOUS_CUSTOMER_RECORD" } : { reason: "INSUFFICIENT_DATA" };
  return (
    <div className="why-grid">
      <div className="why-col">
        <div className="why-head">
          <span className="t-HUMAN_REVIEW tier-chip"><span className="dot" />Human review</span>
          <span className="why-total">{reviewTotal} hits</span>
        </div>
        <ul className="reason-bars">
          {Object.entries(reasons).map(([k, n], i) => (
            <li key={k} style={{ ["--i" as string]: i }}>
              <button className="reason-bar" onClick={() => onFilter("HUMAN_REVIEW", filterFor(k))}>
                <div className="rb-top">
                  <span className="rb-title">{REASON_TEXT[k]?.title ?? k}</span>
                  <span className="rb-n">{n}<span className="rb-pct">{((n / reviewTotal) * 100).toFixed(0)}%</span></span>
                </div>
                <div className="rb-track"><span style={{ width: `${(n / max) * 100}%` }} /></div>
                <div className="rb-body">{REASON_TEXT[k]?.body}</div>
              </button>
            </li>
          ))}
        </ul>
      </div>
      <div className="why-col">
        <div className="why-head">
          <span className="t-BORDERLINE tier-chip"><span className="dot" />Borderline · 4-eyes</span>
          <span className="why-total">{border.length} hits</span>
        </div>
        <p className="why-lede">The engine has decided, but a second analyst must confirm before the case closes.</p>
        <ul className="reason-bars compact">
          <li style={{ ["--i" as string]: 0 }}>
            <button className="reason-bar" onClick={() => onFilter("BORDERLINE", { engine: "ENGINES_DISAGREE" })}>
              <div className="rb-top"><span className="rb-title">The two engines disagree</span><span className="rb-n">{disagree}</span></div>
              <div className="rb-track"><span style={{ width: `${(disagree / Math.max(1, border.length)) * 100}%` }} /></div>
            </button>
          </li>
          {weakTop.slice(0, 4).map(([f, n], i) => (
            <li key={f} style={{ ["--i" as string]: i + 1 }}>
              <button className="reason-bar" onClick={() => onFilter("BORDERLINE", { flag: f === "OTHER" ? "" : f })}>
                <div className="rb-top"><span className="rb-title">Weak fact: {f === "OTHER" ? "other" : humanFlag(f)}</span><span className="rb-n">{n}</span></div>
                <div className="rb-track"><span style={{ width: `${(n / Math.max(1, border.length)) * 100}%` }} /></div>
              </button>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------ two engines, one decision */

function EnginesStrip({ s }: { s: Summary }) {
  const a = s.engines?.agreement ?? {};
  return (
    <div className="engines-strip">
      <div className="es-engine">
        <div className="es-kicker">Engine 1 · decides</div>
        <h4>Decision core</h4>
        <p>AML Bot reference engine: an 8-step rule order, Head-of-FinCrime policy calls and its own second check of every clearance.</p>
      </div>
      <div className="es-plus" aria-hidden>+</div>
      <div className="es-engine">
        <div className="es-kicker">Engine 2 · re-decides independently</div>
        <h4>Independent check</h4>
        <p>Six rule modules (entity type, deceased, timeline, DOB, name, location) and a fixed-precedence compiler. Every facet cites record IDs.</p>
      </div>
      <div className="es-arrow" aria-hidden>
        <svg viewBox="0 0 40 24"><path d="M2 12h32M26 4l8 8-8 8" /></svg>
      </div>
      <ul className="es-rules">
        <li className="r-agree">
          <span className="es-n">{a.AGREE ?? "—"}</span>
          <span><b>Agree</b> → automated, tier by evidence strength</span>
        </li>
        <li className="r-disagree">
          <span className="es-n">{a.DISAGREE ?? 0}</span>
          <span><b>Disagree</b> → core decides, second analyst confirms (4-eyes)</span>
        </li>
        <li className="r-conflict">
          <span className="es-n">{a.CONFLICT ?? 0}</span>
          <span><b>Conflict</b> (core clears, check confirms) → never cleared, goes to review</span>
        </li>
      </ul>
    </div>
  );
}

/* ------------------------------------------------------------------ page */

export default function Overview({ s, o, rows, onTier, onFilter, onStartTour, onRepro }: {
  s: Summary | null;
  o: OverviewData | null;
  rows: Row[];
  onTier: (t: Tier) => void;
  onFilter: (tier: Tier, f: { reason?: string; engine?: string; flag?: string }) => void;
  onStartTour: () => void;
  onRepro: () => void;
}) {
  const ev = s?.eval;
  const repro: ReproState | undefined = s?.repro;
  const rep = repro?.report;
  const runs = rep?.runs.length ?? 0;
  const tiers = o?.tiers ?? s?.tiers ?? {};
  const runSec = useMemo(() => {
    const per = rep?.runs.map((r) => r.seconds).filter((x) => typeof x === "number") ?? [];
    if (per.length) return per.reduce((a, b) => a + b, 0) / per.length;
    return s ? s.run_seconds + s.load_seconds : null;
  }, [rep, s]);
  const people = (tiers.BORDERLINE ?? 0) + (tiers.HUMAN_REVIEW ?? 0);

  return (
    <section className="overview" aria-label="Overview">
      <div className="hero">
        <div className="hero-copy">
          <div className="eyebrow">Sanctions &amp; PEP screening · {s?.dataset?.customers?.toLocaleString() ?? "—"} customer records · engine {s?.engine_version ?? "—"}</div>
          <h1 className="hero-title">
            <span className="ht-line"><Num value={o?.hits ?? s?.hits} /> hits.</span>
            <span className="ht-line">Two deterministic engines.</span>
            <span className="ht-line ht-accent">People see only what policy says they must.</span>
          </h1>
          <div className="hero-cta">
            <button className="btn primary big" onClick={onStartTour}>
              <svg viewBox="0 0 16 16" aria-hidden className="ico"><path d="M4 2.5v11l9-5.5z" /></svg>
              Start guided demo
            </button>
            <button className="btn ghost big" onClick={() => onTier("HUMAN_REVIEW")}>Open the review queue</button>
          </div>
        </div>
        <div className="hero-figures">
          <Stat label="Fully automated" tone="lead" i={0}
            sub={<>{(tiers.AUTO_CLEAR ?? 0) + (tiers.AUTO_ESCALATE ?? 0)} hits closed with no person</>}>
            <Num value={o?.fully_automated_pct} decimals={1} suffix="%" />
          </Stat>
          <Stat label="Unsafe clearances" tone="safe" i={1}
            sub={ev ? <>true matches cleared, on {ev.labelled_hits} practice labels</> : "no practice labels"}>
            {ev ? <Num value={ev.unsafe_clearances} /> : "n/a"}
          </Stat>
          <Stat label="Decision score" i={2} sub={ev ? <>cost saved vs all-review · {Math.round((ev.decision_accuracy ?? 0) * (ev.labelled_hits ?? 0))}/{ev.labelled_hits} correct</> : ""}>
            {ev?.decision_score !== null && ev?.decision_score !== undefined ? <Num value={ev.decision_score} decimals={4} delay={150} /> : "—"}
          </Stat>
        </div>
      </div>

      <div className="stat-strip">
        <Stat label="Hits screened" i={3} sub={`${o?.alerts ?? "—"} alerts`}><Num value={o?.hits ?? s?.hits} /></Stat>
        <Stat label="Decided by engine" i={4} sub="cleared or confirmed"><Num value={o?.decided_by_engine_pct} decimals={1} suffix="%" /></Stat>
        <Stat label="Engine agreement" i={5} sub={`${o?.engines_disagree ?? 0} disagree · ${o?.engines_conflict ?? 0} conflict`}>
          <Num value={o?.engine_agreement_pct} decimals={1} suffix="%" />
        </Stat>
        <Stat label="Reproducible" i={6} tone={rep ? (rep.byte_identical ? "safe" : "bad") : ""}
          sub={repro?.running ? "running 5 fresh runs…" : rep ? <>byte-identical · <button className="link" onClick={onRepro}>re-check</button></> : <button className="link" onClick={onRepro}>run 5× check</button>}>
          {rep ? <span className="num">{rep.byte_identical ? runs : 0}/{runs}</span> : <span className="muted">—</span>}
        </Stat>
        <Stat label="One full run" i={7} sub="limit is 30 min"><Num value={runSec} prefix="~" suffix=" s" /></Stat>
        <Stat label="Model cost" i={8} sub="no LLM in the decision path"><span className="num">£0</span></Stat>
      </div>

      <div className="ov-section">
        <div className="ov-head">
          <h2>Where every hit goes</h2>
          <p>Each of the {o?.hits ?? s?.hits ?? "—"} hits passes both engines, then lands in exactly one tier. <b>{people}</b> reach a person; the rest close on evidence. Click a tier to open its queue.</p>
        </div>
        <div className="flow-wrap">
          <FlowVisual tiers={tiers} total={o?.hits ?? s?.hits ?? 0} onTier={onTier} />
        </div>
      </div>

      <div className="ov-section">
        <div className="ov-head">
          <h2>Why people still review</h2>
          <p>Not guesswork: every hit a person sees is there because a written rule sends it there. Click a reason to see those hits.</p>
        </div>
        {o && <Breakdown reasons={o.review_reasons} rows={rows} onFilter={onFilter} />}
      </div>

      <div className="ov-section">
        <div className="ov-head">
          <h2>Two engines, one decision</h2>
          <p>Dual control in code. The engines are built independently; their disagreements are routed, never averaged away.</p>
        </div>
        {s && <EnginesStrip s={s} />}
      </div>
    </section>
  );
}
