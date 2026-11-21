import { useEffect, useState } from "react";
import { api, type ReviewRow } from "./api";
import { Badge, StatusPill } from "./ui";

export default function Audit({ version, onOpen }: { version: number; onOpen: (hitId: string) => void }) {
  const [rows, setRows] = useState<ReviewRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  useEffect(() => {
    api.reviews().then(setRows).catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, [version]);
  const overrides = rows?.filter((r) => r.action === "override").length ?? 0;
  const analysts = new Set(rows?.map((r) => r.analyst.toLowerCase()) ?? []).size;
  return (
    <section className="page">
      <div className="page-head">
        <div>
          <h2>Audit trail</h2>
          <p className="muted">
            Every analyst action, newest first: who, when, what the engine said and what the person decided, with the note.
            Append-only; the engine's own output is never changed.
          </p>
        </div>
        <a className="btn primary" href="/api/reviews.csv" download>Export audit trail (CSV)</a>
      </div>
      {err && <div className="banner error">{err}</div>}
      {rows && (
        <div className="small muted stat-line">
          {rows.length} actions · {overrides} overrides · {analysts} {analysts === 1 ? "analyst" : "analysts"}
        </div>
      )}
      {rows && !rows.length && (
        <div className="empty-box">
          <strong>No analyst actions yet.</strong>
          <span className="muted">Open a hit from the review queue and accept or override it with a note; it appears here.</span>
        </div>
      )}
      {rows && rows.length > 0 && (
        <table className="grid audit">
          <thead>
            <tr><th>#</th><th>When (UTC)</th><th>Analyst</th><th>Hit</th><th>Engine</th><th>Action</th><th>Decision</th><th>Case</th><th>Note</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.seq}>
                <td className="mono">{r.seq}</td>
                <td className="mono nowrap">{r.at.replace("T", " ").replace("+00:00", "")}</td>
                <td>{r.analyst}</td>
                <td className="nowrap"><button className="link mono" onClick={() => onOpen(r.hit_id)}>{r.hit_id}</button></td>
                <td><Badge decision={r.engine_decision} /> <span className="mono small muted">{r.engine_reason_code}</span></td>
                <td className={r.action === "override" ? "strong" : ""}>{r.action}</td>
                <td><Badge decision={r.decision} /></td>
                <td>{r.case_status && <StatusPill status={r.case_status} />}</td>
                <td className="note-cell">{r.note}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
