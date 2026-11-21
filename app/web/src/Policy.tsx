import type { Summary } from "./api";
import { FLAG_LABEL, POLICY, TIERS } from "./labels";
import { TierPill } from "./ui";

function RuleStat({ id, s }: { id: string; s: Summary | null }) {
  const st = s?.rule_stats?.[id];
  const flagCount = s?.flags?.[id];
  if (st) {
    const total = Object.values(st).reduce((a, b) => a + b, 0);
    return (
      <span className="rule-stat" title={JSON.stringify(st)}>
        <span className="mono strong">{id}</span>{" "}
        <span className="muted">
          {total} checks · {st.contradiction ?? 0} contradict · {st.agreement ?? 0} agree
        </span>
      </span>
    );
  }
  if (id.startsWith("P6.")) {
    return <span className="rule-stat"><span className="mono strong">{id}</span> <span className="muted">no output this run</span></span>;
  }
  return (
    <span className="rule-stat">
      <span className="mono strong">{id}</span>
      {id in FLAG_LABEL
        ? <span className="muted"> flag · {flagCount ?? 0} hits this run</span>
        : <span className="muted"> engine step</span>}
    </span>
  );
}

export default function Policy({ s }: { s: Summary | null }) {
  return (
    <section className="page">
      <div className="page-head">
        <div>
          <h2>Policy &amp; controls</h2>
          <p className="muted">
            The screening procedure, paraphrased, and the rule ids that implement each principle. Every line of the rule
            trace carries one of these ids, so any decision maps back to the clause that produced it.
          </p>
        </div>
      </div>

      <div className="policy-grid">
        {POLICY.map((p) => (
          <article key={p.id} className="principle">
            <div className="p-id mono">{p.id}</div>
            <div>
              <h3>{p.title}</h3>
              <p>{p.text}</p>
              {p.rules.length > 0 && <div className="rules">{p.rules.map((r) => <RuleStat key={r} id={r} s={s} />)}</div>}
            </div>
          </article>
        ))}
      </div>

      <h3 className="section-title">Routing — what is automated and what needs a person</h3>
      <table className="grid">
        <thead><tr><th>Tier</th><th>When</th><th>Who acts</th><th>This run</th></tr></thead>
        <tbody>
          {TIERS.map((t) => (
            <tr key={t.id}>
              <td><TierPill tier={t.id} long /></td>
              <td>
                {t.id === "AUTO_CLEAR" && "A solid fact contradicts the hit (P6), and nothing about it is weak."}
                {t.id === "AUTO_ESCALATE" && "Name + DOB (person) or name + country (company) agree and nothing contradicts (P7)."}
                {t.id === "BORDERLINE" && "An automated decision whose deciding fact is weak (flag below)."}
                {t.id === "HUMAN_REVIEW" && "Nothing settles it, or several customers lead to different outcomes (P3, P7)."}
              </td>
              <td>{t.who}</td>
              <td className="mono">{s?.tiers?.[t.id] ?? 0}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <h3 className="section-title">Weak-evidence flags (route an automated decision to the 4-eyes check)</h3>
      <table className="grid">
        <thead><tr><th>Flag</th><th>Meaning</th><th>This run</th></tr></thead>
        <tbody>
          {Object.entries(FLAG_LABEL).map(([k, v]) => (
            <tr key={k}><td className="mono">{k}</td><td>{v}</td><td className="mono">{s?.flags?.[k] ?? 0}</td></tr>
          ))}
        </tbody>
      </table>

      <h3 className="section-title">Safety controls</h3>
      <ul className="controls">
        <li>A hit is only cleared on a contradiction; a missing fact never clears. Any engine error becomes a review, never a clearance.</li>
        <li>Clearing a true match costs far more than a needless review, so the engine sends doubtful hits to a person.</li>
        <li>An analyst clearing a hit the engine did not clear needs a second, different analyst (4-eyes).</li>
        <li>Decisions are deterministic: the same input gives byte-identical output (see the reproducibility badge).</li>
        <li>No language model takes part in any decision.</li>
      </ul>
    </section>
  );
}
