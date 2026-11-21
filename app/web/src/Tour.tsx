import { useEffect } from "react";
import type { Tier } from "./api";
import { TIER_BY_ID } from "./labels";

export type Focus = "verdict" | "compare" | "engines" | "reviewer";

export interface TourStep {
  hit: string;
  tier: Tier;
  title: string;
  caption: string;
  /** Record ids to spotlight in the case file. */
  spotlight: string[];
  /** Case-file section to scroll to and ring. */
  focus: Focus;
}

/** Real dev hits, checked with `screen explain` (docs/DEMO.md). */
export const TOUR: TourStep[] = [
  {
    hit: "H-58A39C",
    tier: "AUTO_CLEAR",
    title: "Cleared on a hard contradiction",
    caption:
      "The customer's approved KYC check gives a date of birth two and a half years after the listed person's only DOB, and nothing the bank holds agrees. Both engines reached this independently. One record decides it.",
    spotlight: ["KYC-232AE0"],
    focus: "compare",
  },
  {
    hit: "H-4D76E3",
    tier: "AUTO_ESCALATE",
    title: "Confirmed match, straight to compliance",
    caption:
      "Two customer records merge into one person by phone and DOB. The record DOB is off, but the latest approved check matches the hit, as do name and both countries. Both engines confirm, so no analyst time is spent.",
    spotlight: ["CUS-970916", "CUS-906311", "NOTE-4E8A14"],
    focus: "verdict",
  },
  {
    hit: "H-B1018D",
    tier: "HUMAN_REVIEW",
    title: "A policy call, with an instruction for the reviewer",
    caption:
      "Literally a location mismatch: Serbia against Montenegro. But they were one state, so listing data may name either. The Head of Financial Crime's rule: escalate, never clear, and tell the reviewer exactly what to check.",
    spotlight: [],
    focus: "reviewer",
  },
  {
    hit: "H-2C6B2E",
    tier: "BORDERLINE",
    title: "The engines disagree: four eyes",
    caption:
      "The core confirms on a birth year from a web search result; the independent check reads a full DOB in the hit's profile that differs from the customer's. The escalation stands, but a second, different analyst must confirm it.",
    spotlight: ["SR-1054CF", "NOTE-BCDE4B"],
    focus: "engines",
  },
];

export function TourBar({ step, setStep, onExit, onFinish }: { step: number; setStep: (n: number) => void; onExit: () => void; onFinish: () => void }) {
  const s = TOUR[step];
  const last = step === TOUR.length - 1;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement | null;
      if (t && (["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName) || t.closest(".xterm"))) return;
      if (e.key === "ArrowRight" || e.key === "PageDown") {
        e.preventDefault();
        e.stopImmediatePropagation();
        if (last) onFinish(); else setStep(step + 1);
      } else if (e.key === "ArrowLeft" || e.key === "PageUp") {
        e.preventDefault();
        e.stopImmediatePropagation();
        if (step > 0) setStep(step - 1);
      } else if (e.key === "Escape") {
        e.preventDefault();
        onExit();
      }
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [step, last, setStep, onExit, onFinish]);

  return (
    <div className={`tour-bar t-${s.tier}`} role="dialog" aria-label="Guided demo" aria-live="polite">
      <div className="tour-progress" aria-hidden>
        {TOUR.map((x, i) => (
          <button key={x.hit} className={`tp-seg t-${x.tier} ${i === step ? "on" : ""} ${i < step ? "done" : ""}`}
            onClick={() => setStep(i)} tabIndex={-1} title={`${i + 1}. ${x.hit}`} />
        ))}
      </div>
      <div className="tour-main" key={step}>
        <div className="tour-meta">
          <span className="tour-count">{step + 1}<span>/{TOUR.length}</span></span>
          <span className="tier-chip"><span className="dot" />{TIER_BY_ID[s.tier].short}</span>
          <span className="mono tour-hit">{s.hit}</span>
        </div>
        <h3 className="tour-title">{s.title}</h3>
        <p className="tour-caption">{s.caption}</p>
      </div>
      <div className="tour-nav">
        <div>
        <button className="btn ghost" onClick={() => setStep(step - 1)} disabled={step === 0} aria-label="Previous step">
          <svg viewBox="0 0 16 16" className="ico" aria-hidden><path d="M10 3 5 8l5 5" /></svg>
        </button>
        <button className="btn primary" onClick={() => (last ? onFinish() : setStep(step + 1))}>
          {last ? "Back to overview" : "Next"}
          <svg viewBox="0 0 16 16" className="ico" aria-hidden><path d="m6 3 5 5-5 5" /></svg>
        </button>
        </div>
        <button className="link tour-exit" onClick={onExit}>Exit <kbd>Esc</kbd></button>
      </div>
    </div>
  );
}
