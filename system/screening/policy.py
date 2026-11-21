"""Policy registry: which rule of the SOP / Head of FinCrime / dual control a decision rests on.

Standard library only. Three families:

  P1..P9   SOP principles (data/dev/POLICY.md), paraphrased in one line each - not the SOP text.
  HOFC-1..9  policy calls by the Head of Financial Crime (contrib/aml-bot/README.md). HOFC-1..8 are
           the numbered calls there; HOFC-9 is the stress-test hardening rule from the same README
           ("missing or garbled data only moves hits towards REVIEW; every clearance is re-derived").
  DC-1..3  dual-control merge rules between the decision core and the independent check
           (compiler.merge_with_core).

`policies_for(decision)` maps one decision (HitDecision or its dict form) to the ids that apply,
deterministically, from its reason code, flags, customer id, evidence ids and reason text.
P1 (identity, not policy) and P9 (cite what you used) apply to every hit and are not listed per hit.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Policy:
    id: str
    kind: str  # "sop" | "hofc" | "dual_control"
    title: str
    effect: str
    implemented_by: tuple[str, ...]  # where in the code the rule lives
    triggers: tuple[str, ...] = ()  # reason codes / flags / reason-text markers policies_for uses

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["implemented_by"] = list(self.implemented_by)
        d["triggers"] = list(self.triggers)
        return d


ALL_HITS = "(applies to every hit)"

REGISTRY: tuple[Policy, ...] = (
    # ------------------------------------------------------------------ SOP principles
    Policy("P1", "sop", "Identity only",
           "Decide only whether the hit is this customer; the listing's severity is irrelevant.",
           ("whole engine: no rule looks at listing severity",), (ALL_HITS,)),
    Policy("P2", "sop", "Find the customer first",
           "Follow the alert's link (or search by screened details) before comparing; never borrow "
           "another customer's records. No customer found -> REVIEW with a null customer id.",
           ("screening.resolve.resolve_customer", "core: aml_engine.resolve_customer"),
           ("customer_record_id is null",)),
    Policy("P3", "sop", "One person, one picture",
           "Records linked by phone + full DOB or by document number are one person; several people "
           "fitting an alert are decided separately (same answer kept, else AMBIGUOUS_CUSTOMER_RECORD).",
           ("screening.identity", "compiler.combine_persons", "core: aml_engine.decide"),
           ("AMBIGUOUS_CUSTOMER_RECORD", "customer_record_id AMBIGUOUS")),
    Policy("P4", "sop", "Verified beats declared",
           "The latest approved identity check (KYC or onboarding note) is the source for legal name, "
           "DOB and document country; rejected/expired checks are ignored.",
           ("screening.identity.verified_identities", "core: aml_engine.person_profile"),
           ("NAME_MISMATCH_VERIFIED", "KYC-* evidence", "'verified' in reason")),
    Policy("P5", "sop", "Gather hit facts everywhere",
           "A hit's DOB can come from its dobs field, an age in the profile notes or a matching search "
           "result; dated reports are the fixed 'According to ...' lines.",
           ("screening.facts.gather_hit_facts", "core: aml_engine.hit_profile"),
           ("TEMPORAL_IMPOSSIBILITY", "DOB_FROM_SEARCH_RESULT", "SR-* evidence", "'profile notes' in reason")),
    Policy("P6", "sop", "Clear only on a contradiction",
           "DIFFERENT_ENTITY needs a contradicting fact (time, DOB, location, verified name, type); a "
           "country difference never clears a name + DOB match.",
           ("screening.rules.*", "compiler.compile_person", "core: aml_engine.decide_one"),
           ("any DIFFERENT_ENTITY", "'not cleared' / 'does not clear' in reason")),
    Policy("P7", "sop", "Confirm only on agreement",
           "SAME_ENTITY for a person needs name + DOB agreement, for a company name + country; anything "
           "else without a contradiction is REVIEW.",
           ("compiler.compile_person", "core: aml_engine.decide_one"),
           ("any SAME_ENTITY", "INSUFFICIENT_DATA")),
    Policy("P8", "sop", "Name comparison",
           "Names agree on two matching parts (variants, joined parts, small edit distance) or every part "
           "of the shorter name; placeholders and company legal forms are not parts.",
           ("screening.normalize.names", "screening.rules.name", "core: aml_engine.Names"),
           ("NAME_MISMATCH_VERIFIED", "SAME_ENTITY codes", "NAME_FUZZY_MATCH", "'name part' in reason")),
    Policy("P9", "sop", "Cite what decided it",
           "Every decision names the record ids it relied on and the resolved customer record.",
           ("screening.explain.evidence_ids", "core: R(..., evidence)"), (ALL_HITS,)),
    # ------------------------------------------------------------- Head of FinCrime calls
    Policy("HOFC-1", "hofc", "Single shared name part",
           "Verified name shares only one part with the hit: clear only if the given names are full and "
           "clearly differ, otherwise REVIEW with reviewer guidance.",
           ("core: aml_engine.decide_one (name branch)", "flag ESCALATED_BY_POLICY"),
           ("'shares only one name part' in reason",)),
    Policy("HOFC-2", "hofc", "Patronymic-style surnames",
           "A shared surname that is commonly a father's given name (Umar, Ahmed, Ibrahim...) makes name "
           "order unreliable: REVIEW.",
           ("core: aml_engine.is_patronymic_surname", "core: aml_engine.given_names_differ"), ("\"father's given name\" in reason",)),
    Policy("HOFC-3", "hofc", "Former single states",
           "Countries that were one state (ex-Yugoslavia, USSR, Czechoslovakia, Sudan/South Sudan, "
           "Ethiopia/Eritrea) do not give a location clearance: REVIEW.",
           ("core: aml_engine.shared_former_state",), ("'part of one state' in reason",)),
    Policy("HOFC-4", "hofc", "Business-address-only country",
           "An individual hit whose only country is a business address does not support a location "
           "clearance: REVIEW.",
           ("core: aml_engine.decide_one (business_only)",), ("'business address' in reason",)),
    Policy("HOFC-5", "hofc", "Ambiguous customer, all clear",
           "Several customers fit and every one leads to clearance: clear with customer AMBIGUOUS.",
           ("core: aml_engine.decide", "compiler.combine_persons"),
           ("DIFFERENT_ENTITY with customer_record_id AMBIGUOUS",)),
    Policy("HOFC-6", "hofc", "Ages in news paragraphs ignored",
           "Only the first profile-notes paragraph yields an age-derived DOB; ages inside news reports "
           "are ignored (safe direction).",
           ("core: aml_engine.age_years / hit_profile", "screening.facts"),
           ("'profile notes' in reason",)),
    Policy("HOFC-7", "hofc", "Note countries count",
           "Every country mentioned in the customer's notes counts as tied to the customer for the "
           "location rule.",
           ("core: aml_engine.person_profile (country_notes)", "screening.identity.country_claims"),
           ("LOCATION_MISMATCH with NOTE-* evidence",)),
    Policy("HOFC-8", "hofc", "Possible name reorder left in REVIEW",
           "H-2808BE (possible name reorder) stays REVIEW rather than being tuned to the practice label.",
           ("core: aml_engine (no special case; deliberate non-tuning)",), ("hit_id H-2808BE",)),
    Policy("HOFC-9", "hofc", "Garbled data only moves to REVIEW",
           "Unreadable or missing data never manufactures a clearance, and an independent verifier "
           "re-derives every clearance before it stands (stress-test hardening).",
           ("core: aml_engine.second_check", "core: aml_engine.error_review", "pipeline.fallback"),
           ("'second check' / 'could not complete' / 'could not be read' / 'share the customer's phone' "
            "in reason", "ENGINE_ERROR / CHECK_ERROR flags")),
    # ------------------------------------------------------------------- dual control
    Policy("DC-1", "dual_control", "Core clears, check confirms",
           "The core says DIFFERENT_ENTITY but the independent check confirms (SAME_ENTITY): never clear "
           "on a conflict -> REVIEW / HUMAN_REVIEW.",
           ("compiler.merge_with_core",), ("flag ENGINES_CONFLICT",)),
    Policy("DC-2", "dual_control", "Engines disagree",
           "Any other disagreement keeps the core's decision but routes it to 4-eyes QA (BORDERLINE "
           "unless the decision is already REVIEW).",
           ("compiler.merge_with_core",), ("flag ENGINES_DISAGREE",)),
    Policy("DC-3", "dual_control", "Core missing",
           "If the core gives no answer the check alone may never clear: its clearance becomes REVIEW.",
           ("compiler.merge_with_core",), ("flag CORE_MISSING",)),
)

BY_ID: dict[str, Policy] = {p.id: p for p in REGISTRY}
_ORDER = {p.id: i for i, p in enumerate(REGISTRY)}

_DIFFERENT = {"TEMPORAL_IMPOSSIBILITY", "DOB_MISMATCH", "LOCATION_MISMATCH", "NAME_MISMATCH_VERIFIED",
              "ENTITY_TYPE_MISMATCH", "DECEASED"}
_SAME = {"NAME_DOB_LOCATION_MATCH", "NAME_DOB_MATCH", "NAME_LOCATION_MATCH"}

# reason-text markers (lower-case) -> policy id
_TEXT_MARKERS: tuple[tuple[str, str], ...] = (
    ("shares only one name part", "HOFC-1"),
    ("father's given name", "HOFC-2"),
    ("part of one state", "HOFC-3"),
    ("business address", "HOFC-4"),
    ("profile notes", "HOFC-6"),
    ("profile notes", "P5"),
    ("second check", "HOFC-9"),
    ("could not complete", "HOFC-9"),
    ("could not be read", "HOFC-9"),
    ("share the customer's phone", "HOFC-9"),
    ("could not evaluate", "HOFC-9"),
    ("independent rule check confirms", "DC-1"),
    ("decision core gave no answer", "DC-3"),
    ("not cleared", "P6"),
    ("does not clear", "P6"),
    ("verified", "P4"),
    ("name part", "P8"),
)


def _value(obj: Any, name: str) -> Any:
    v = obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
    return getattr(v, "value", v)


def policies_for(decision: Any) -> list[str]:
    """Policy ids that apply to one decision (HitDecision or dict), in registry order."""
    code = str(_value(decision, "reason_code") or "")
    dec = str(_value(decision, "decision") or "")
    customer = _value(decision, "customer_record_id")
    flags = {str(f) for f in (_value(decision, "flags") or ())}
    evidence = [str(e) for e in (_value(decision, "evidence") or ())]
    reason = str(_value(decision, "reason") or "").lower()
    hit_id = str(_value(decision, "hit_id") or "")
    ids: set[str] = set()

    if customer is None:
        ids.add("P2")
    if customer == "AMBIGUOUS" or code == "AMBIGUOUS_CUSTOMER_RECORD":
        ids.add("P3")
    if dec == "DIFFERENT_ENTITY" or code in _DIFFERENT:
        ids.add("P6")
        if customer == "AMBIGUOUS":
            ids.add("HOFC-5")
    if dec == "SAME_ENTITY" or code in _SAME or code == "INSUFFICIENT_DATA":
        ids.add("P7")
    if code in _SAME or code == "NAME_MISMATCH_VERIFIED" or "NAME_FUZZY_MATCH" in flags:
        ids.add("P8")
    if code == "NAME_MISMATCH_VERIFIED" or any(e.startswith("KYC-") for e in evidence):
        ids.add("P4")
    if (code == "TEMPORAL_IMPOSSIBILITY" or "DOB_FROM_SEARCH_RESULT" in flags
            or any(e.startswith("SR-") for e in evidence)):
        ids.add("P5")
    if code == "LOCATION_MISMATCH" and any(e.startswith("NOTE-") for e in evidence):
        ids.add("HOFC-7")
    if hit_id == "H-2808BE":
        ids.add("HOFC-8")
    if any(f.startswith(("ENGINE_ERROR", "CHECK_ERROR")) for f in flags):
        ids.add("HOFC-9")
    if "ENGINES_CONFLICT" in flags:
        ids.add("DC-1")
    if "ENGINES_DISAGREE" in flags:
        ids.add("DC-2")
    if "CORE_MISSING" in flags:
        ids.add("DC-3")
    for marker, pid in _TEXT_MARKERS:
        if marker in reason:
            ids.add(pid)
    return sorted(ids, key=_ORDER.__getitem__)


def registry() -> list[dict[str, Any]]:
    return [p.to_dict() for p in REGISTRY]
