import type { CaseStatus, Tier } from "./api";

/** Tier order = the order a human works them (HITL.md): people first, automation last. */
export const TIERS: { id: Tier; key: string; label: string; short: string; who: string }[] = [
  { id: "HUMAN_REVIEW", key: "1", label: "Needs analyst review", short: "Analyst review", who: "An analyst decides" },
  { id: "BORDERLINE", key: "2", label: "Borderline — 4-eyes check", short: "Borderline", who: "A second analyst confirms or overrides" },
  { id: "AUTO_ESCALATE", key: "3", label: "Auto-escalated to compliance", short: "Auto-escalated", who: "Compliance handles the true match" },
  { id: "AUTO_CLEAR", key: "4", label: "Auto-cleared (hard contradiction)", short: "Auto-cleared", who: "No action; QA samples it" },
];

export const TIER_BY_ID = Object.fromEntries(TIERS.map((t) => [t.id, t])) as Record<Tier, (typeof TIERS)[number]>;

export const DECISION_LABEL: Record<string, string> = {
  SAME_ENTITY: "Same entity",
  DIFFERENT_ENTITY: "Different entity",
  REVIEW: "Review",
};

export const REASON_LABEL: Record<string, string> = {
  TEMPORAL_IMPOSSIBILITY: "Timeline impossible",
  DOB_MISMATCH: "Date of birth differs",
  LOCATION_MISMATCH: "Location differs",
  NAME_MISMATCH_VERIFIED: "Verified name differs",
  ENTITY_TYPE_MISMATCH: "Person vs company",
  DECEASED: "Hit is deceased",
  NAME_DOB_LOCATION_MATCH: "Name, DOB and country agree",
  NAME_DOB_MATCH: "Name and DOB agree",
  NAME_LOCATION_MATCH: "Name and country agree",
  INSUFFICIENT_DATA: "Not enough to decide",
  AMBIGUOUS_CUSTOMER_RECORD: "Several customers fit",
};

export const FLAG_LABEL: Record<string, string> = {
  DOB_FROM_SEARCH_RESULT: "hit DOB comes only from a web search result",
  DOB_FROM_AGE_AT_DATE: "hit birth year derived from an age at a date",
  NAME_EDIT_DISTANCE_ONLY: "names match only by spelling tolerance",
  FACT_FROM_NOTE: "customer fact taken from a support note, not a KYC check",
  NO_CUSTOMER_RECORD: "no customer record resolved — decided on screened details only",
  SINGLE_LOCATION_CONTRADICTION: "clearance rests on a single country comparison",
  DECLARED_NOT_VERIFIED: "customer data is declared, not verified by KYC",
  PLACEHOLDER_NAME: "customer name contains placeholder parts",
};

export const STATUS_LABEL: Record<CaseStatus, string> = {
  AUTOMATED: "Automated",
  OPEN: "Open",
  AWAITING_SECOND: "Awaiting 2nd reviewer",
  CLOSED: "Closed",
};

export const FACET_LABEL: Record<string, string> = {
  type: "Entity type",
  deceased: "Life status",
  time: "Timeline",
  dob: "Date of birth",
  location: "Location",
  name: "Name",
};

export function humanFlag(f: string): string {
  return FLAG_LABEL[f] ?? f.toLowerCase().replace(/_/g, " ");
}

export function reasonLabel(code: string): string {
  return REASON_LABEL[code] ?? code;
}

/** One plain-language line: why the engine routed this hit to this tier. */
export function whyTier(r: { tier: Tier; decision: string; reason_code: string; flags: string[] }): string {
  const reason = reasonLabel(r.reason_code).toLowerCase();
  switch (r.tier) {
    case "AUTO_CLEAR":
      return `Cleared: ${reason} — a hard contradiction on solid evidence.`;
    case "AUTO_ESCALATE":
      return `Escalated: ${reason}, nothing contradicts the hit.`;
    case "BORDERLINE": {
      const what = DECISION_LABEL[r.decision]?.toLowerCase() ?? r.decision;
      const weak = r.flags.length ? r.flags.map(humanFlag).join("; ") : "weak evidence";
      return `Engine says ${what} (${reason}), but ${weak}.`;
    }
    default:
      if (r.reason_code === "AMBIGUOUS_CUSTOMER_RECORD")
        return "Several customers fit the alert and they lead to different outcomes.";
      return "Nothing contradicts the hit and agreement is not strong enough to confirm it.";
  }
}

/** SOP principles, paraphrased, with the rule ids that implement them. */
export const POLICY: { id: string; title: string; text: string; rules: string[] }[] = [
  { id: "P1", title: "Identity only", text: "Decide whether the hit is the customer; risk and sanctions handling are out of scope.", rules: [] },
  { id: "P2", title: "Find the customer first", text: "Resolve the screened party to customer records through account, login or email links, else use the screened details.", rules: ["resolve"] },
  { id: "P3", title: "One person, several records", text: "Merge records of the same person; if several different people fit, decide each and treat disagreement as ambiguous.", rules: ["resolve", "combine_persons"] },
  { id: "P4", title: "Verified beats declared", text: "The latest approved identity check outranks self-declared data; notes and declared data are flagged as weaker.", rules: ["FACT_FROM_NOTE", "DECLARED_NOT_VERIFIED"] },
  { id: "P5", title: "Hit facts", text: "Hit facts come from list fields, profile notes and search results that are about the hit.", rules: ["DOB_FROM_SEARCH_RESULT", "DOB_FROM_AGE_AT_DATE"] },
  { id: "P6", title: "Clear only on a contradiction", text: "A hit is cleared only when a solid fact rules it out: type, death, timeline, DOB, verified name or location. A missing fact is never a contradiction.", rules: ["P6.type", "P6.deceased", "P6.temporal", "P6.dob", "P6.name", "P6.location"] },
  { id: "P7", title: "Confirm only on agreement", text: "Confirm a person on name + DOB, a company on name + country, with nothing contradicting; otherwise send to review.", rules: ["compiler"] },
  { id: "P8", title: "Name comparison", text: "Names are compared after normalisation, known variants and transliteration; spelling tolerance alone is weak.", rules: ["P6.name", "NAME_EDIT_DISTANCE_ONLY"] },
  { id: "P9", title: "Cite the deciding records", text: "Every decision names the record IDs that decided it, nothing more.", rules: ["evidence"] },
];
