# Results (standard scorecard)

Generated 2026-11-21 15:20 by `python tools/scorecard.py` (engine 0.1.0, Windows ARM64, Python 3.14.0). **Quote numbers only from this file.** Dev set: 819 hits, 440 alerts, 72,265 customer records.

## 1. Accuracy on the 50 practice labels (the organisers' cost model)

| Metric | Value |
|---|---|
| Decision score (share of cost saved vs. all-REVIEW) | **0.9722** |
| Correct decisions | 49 / 50 |
| Unsafe clearances (true match cleared, cost 60 each) | **0** |
| Reason code right (when decision right) | 100% |
| Customer record right (when decision right) | 100% |
| Evidence precision / recall | 1.0 / 0.9907 |
| Review rate | 22% (true REVIEW labels: 20%) |

## 2. All dev hits: what is automated, what needs a human

| Tier | Hits | Meaning |
|---|---|---|
| Auto-cleared | 396 | hard contradiction, both engines agree |
| Auto-escalated | 98 | confirmed match -> compliance team |
| Borderline (4-eyes) | 59 | decided, but engines disagree or a fact is weak |
| Human review | 266 | SOP says REVIEW |

- Fully automated: **60.3%**; decided by the engine (cleared or confirmed): **67.5%**; cleared: 52.6%; alerts fully closed: 155 / 440.
- Two engines agree on **99.4%** of hits (5 disagree, 0 conflict).
- Why hits go to review:
  - name and country agree but no usable hit DOB (SOP requires review): 219
  - cautious policy call (Head of FinCrime): 35
  - several customers fit and lead to different outcomes: 12

## 3. Reproducibility and cost

- 5 fresh runs (different PYTHONHASHSEED): byte-identical = **True**, decision reproducibility 100%, sha256 `7d79c788602ff6f2...`
- One run: 49.0 s (of which data load 8.4 s); limit is 30 min. API cost: 0 (no model calls).

## 4. The solution in numbers

- 2 independent deterministic engines (dual control): core = AML Bot reference engine (teammate, PR #28): 8-step rule order + independent second_check; check = 6 rule modules (deceased, dob, entity_type, location, name, temporal) + a fixed-precedence compiler.
- 21 policies in the registry (9 sop, 9 hofc, 3 dual_control); 11 reason codes.
- LLM in the decision path: **False**. Python standard library only: True.
