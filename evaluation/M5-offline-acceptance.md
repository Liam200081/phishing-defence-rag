# M1–M5 offline quantitative acceptance

Date: 2026-10-06. Source HEAD: `237c53a02759117f584ceaed602e89296c18c4e3`.
Scope: existing scripted cases, fixtures, saved traces, and deterministic code. No live LLM request or new test sample was used for this report. All percentages below describe the stated fixture set; they are not held-out quality estimates.

## A. Offline Quantitative Acceptance

| Capability | Existing-data result | Denominator and source | Limit |
| --- | --- | --- | --- |
| Planning/tool routing exact match | 3/3 = 100% | Explicit M4 fixture paths: knowledge (`R`), linked email (`PIR`), email without URLs (`PR`), in `tests/test_m4.py::test_simple_question_short_plan` and `test_email_plan_routes_only_needed_tools` | The 24 scripted cases have `allowed_tools`, not independently labeled `expected_tools`; their routing exact-match is N/A. |
| Routing micro precision / recall | 6/6 = 100% / 6/6 = 100% | The same three fixtures have six expected tool occurrences and six matching actual occurrences. | Fixture-level result only. |
| W0 / A1 mean tool calls | 37/12 = 3.083 / 38/12 = 3.167 | All 12 development Case pairs in `experiments/m5/M5-98428c89cc5c4278802f69c971808c15/partial.json`; `diagnostics.tool_calls`. | Diagnostic trace count. The partial run contains model service errors, so this is not an outcome or formal KPI comparison. |
| Unnecessary tool calls / reduction | N/A / N/A | Independent expected-tool labels do not exist for the 24 Case manifest. The three M4 routing fixtures had 0 unnecessary calls on their tested A1 paths; they do not include matched W0 paths. | The extra A1 `analyze_iocs` call on `PP-5-2` follows an injected timeout and cannot be labeled unnecessary without a separate gold judgment. |
| Initial clarification: required / correct / missed / false | 9 / 9 / 0 / 0 | 24 explicitly asserted initial requests in `tests/test_m3.py`: 9 positive, 15 negative. | Fixture prompts, not the 24 scripted Case manifest. Follow-up questions are excluded from this initial-trigger count. |
| Initial clarification precision / recall | 9/9 = 100% / 9/9 = 100% | Same M3 fixture set; true negatives 15/15. | Does not estimate performance on unseen wording. |
| Fact correction/state | 5/5 fixture scenarios pass | M3 correction/resume and four M4 correction/partition scenarios listed below. | These are scenario-specific assertions, not five independent held-out Case outcomes. |
| Superseded fact / dependent invalidation / unrelated retention | 2/2 / 3/3 / 4/4 | Explicit assertions in the correction fixtures listed below. | Properties have different denominators because each fixture asserts different properties. |
| Verification: supported acceptance | 3/3 | Three supported answer strings in `tests/test_m4.py::test_risk_direction_and_certainty_follow_cited_evidence`. | Deterministic verifier fixture. |
| Verification: invalid citation rejection | 3/3 | Invalid evidence ref, invalid supported answer citation, and invalid contradictory answer citation in `tests/test_m5.py::test_invalid_reference_cannot_become_supported` and `test_contradiction_requires_real_conflicting_evidence_and_na_is_case_level`. | Review-validator fixture, not semantic claim validation. |
| Verification: contradiction rejection | 2/2 | `risk free` and `not suspicious` against the cautious-link evidence in the M4 risk-direction fixture. | Same fixture evidence for both. |
| Verification: negation handling | 2/2 | `not safe` accepted and `not suspicious` rejected in the M4 risk-direction fixture. | Narrow English constructions only. |
| Verification: overconfidence detection | 2/2 | `definitely ... threat` and categorical `malicious` rejected in the M4 risk-direction fixture. | Narrow English constructions only. |
| Recovery route accuracy | 7/7 fixture routes | Replan; answer repair; context rebuild; targeted retrieval; clarification; bounded tool retry; parse retry. Sources listed below. | A route selected by a fixture is not evidence of end-to-end answer quality. |
| Budget termination | 4/4 bounded fixtures terminate as expected | Knowledge gap, tool budget exhaustion, tool failure after bounded retry, repeated bad answer after repair budget in `tests/test_m4.py`. | Zero observed loops in these four fixtures; a universal `infinite loop = 0` guarantee is N/A. |
| Regression | 94 total / 94 passed / 0 failed / 0 skipped | `PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q -p no:cacheprovider tests/test_m1.py tests/test_m2.py tests/test_m3.py tests/test_m4.py tests/test_m5.py tests/smoke_runtime.py` | Five dependency deprecation warnings; no real LLM call. `smoke_embedding.py` is an opt-in network/download and project-writing script and was not run. |

Tool notation: `P=parse_email`, `I=analyze_iocs`, `R=retrieve_knowledge`. `PIRPIR` is two executions for a corrected report, not three cached calls. Actual sequences below are unique non-cached `result_ref` events from the saved trace, cross-checked against `diagnostics.tool_calls`. Every manifest row has an `allowed_tools` list, but no independent `expected_tools` field; therefore expected tools are `N/A` for all 24 rows.

| Scripted Case | Split | Expected tools | W0 actual | A1 actual | Trace source |
| --- | --- | --- | --- | --- | --- |
| PP-1-1 | development | N/A | PIR (3) | PIR (3) | D |
| PP-1-2 | development | N/A | PIR (3) | PIR (3) | D |
| PP-2-1 | development | N/A | PIRPIR (6) | PIRPIR (6) | D |
| PP-2-2 | development | N/A | PIR (3) | PIR (3) | D |
| PP-5-1 | development | N/A | PIR (3) | PIR (3) | D |
| PP-5-2 | development | N/A | PIR (3) | PIRI (4) | D; injected IOC timeout |
| SA-00001-1 | development | N/A | PIR (3) | PIR (3) | D |
| SA-00001-2 | development | N/A | PIR (3) | PIR (3) | D |
| SA-00007-1 | development | N/A | PIR (3) | PIR (3) | D |
| SA-00007-2 | development | N/A | PIR (3) | PIR (3) | D |
| SA-00151-1 | development | N/A | R (1) | R (1) | D |
| SA-00151-2 | development | N/A | PIR (3) | PIR (3) | D |
| PP-300-1 | held_out | N/A | PIR (3) | PIR (3) | H1; A1 has 429 errors |
| PP-300-2 | held_out | N/A | N/A | N/A | No saved W0/A1 trace |
| PP-500-1 | held_out | N/A | N/A | N/A | No saved W0/A1 trace |
| PP-500-2 | held_out | N/A | N/A | N/A | No saved W0/A1 trace |
| PP-600-1 | held_out | N/A | N/A | N/A | No saved W0/A1 trace |
| PP-600-2 | held_out | N/A | N/A | N/A | No saved W0/A1 trace |
| SA-00003-1 | held_out | N/A | PR (2) | PR (2) | H2; historical single-Case paired run |
| SA-00003-2 | held_out | N/A | N/A | N/A | No saved W0/A1 trace |
| SA-00011-1 | held_out | N/A | N/A | N/A | No saved W0/A1 trace |
| SA-00011-2 | held_out | N/A | N/A | N/A | No saved W0/A1 trace |
| SA-00012-1 | held_out | N/A | N/A | N/A | No saved W0/A1 trace |
| SA-00012-2 | held_out | N/A | N/A | N/A | No saved W0/A1 trace |

Trace keys: D = `experiments/m5/M5-98428c89cc5c4278802f69c971808c15/partial.json` (same frozen graph/planning/verification/tool source as current HEAD, but service-error-contaminated); H1 = `experiments/m5/M5-2a42bc17cb494507a11eb56d12e98d69/partial.json`; H2 = `experiments/m5/M5-11eafe7f2c24481fbbfbc0c32a42926e/run.json`. Do not combine these runs into a formal quality KPI.

Clarification count sources: `test_email_path_real_tools_no_unnecessary_question` (0/1), `test_account_impact_question_asks_only_for_decision_changing_facts` (1/2), `test_account_takeover_and_attacker_access_phrasings` (6/18), `test_explicit_event_description_avoids_unnecessary_clarification` (0/1), `test_conflicting_action_report_clarifies_when_advice_changes` (1/1), and `test_clarification_resume_and_fact_correction_in_new_run` (1/1). The notation is positive initial triggers / initial requests. All explicit expectations pass.

Correction sources: `tests/test_m3.py::test_clarification_resume_and_fact_correction_in_new_run`; `tests/test_m4.py::test_fact_correction_supersedes_and_invalidates`, `test_unrelated_fact_correction_retains_knowledge_task`, `test_fact_dependency_invalidation_propagates_to_descendants`, and `test_custom_plan_reuses_only_matching_task_and_params`.

Recovery sources: `tests/test_m4.py::test_replan_reuses_successful_tool_result`, `test_unsupported_answer_detected_and_repaired_once`, `test_context_omission_rebuilds_before_final_answer`, `test_knowledge_gap_uses_targeted_retrieval_then_stops`, `test_clarification_preserves_budget`, `test_tool_failure_is_error_and_retry_is_bounded`, and `test_parse_retry_success_reactivates_ioc_dependency`. Termination sources: knowledge gap, `test_tool_budget_exhaustion_terminates`, bounded tool failure, and `test_repeated_bad_answer_stops_after_repair_budget`.

## B. W0 → A1 improvements supported by data

No W0 → A1 **outcome improvement** is quantified. The M5 fixture `test_planning_and_reflection_ablate_one_factor` shows that Planning ON changes the knowledge query and Reflection ON adds verification; the M4 fixtures demonstrate bounded recovery routes. These are capability/path observations, not improvements in Case Success, Evidence Support, or tool efficiency.

## C. No improvement or regression

In the 12-case development trace D, W0 made 37 tool calls and A1 made 38: A1 averaged 0.083 more calls per Case (about 2.7% more). Eleven Case pairs tied; the injected timeout Case `PP-5-2` accounts for the one-call increase. These traces contain model service errors, so this is a routing diagnostic, not an estimate of typical deployment cost. In the clean historical held-out Case `SA-00003-1`, both W0 and A1 made two tool calls. No unnecessary-call reduction can be computed without gold expected-tool labels.

## D. Not quantifiable from existing data

The 24 scripted Case manifest lacks independent expected-tool and clarification-trigger labels. Thus Case-wide routing exact-match, precision, recall, unnecessary-call counts, and clarification precision/recall are N/A. Most held-out Cases lack W0/A1 saved traces. No complete, independently human-reviewed B1/W0/A1 held-out comparison exists, so quality improvement is N/A. Four bounded fixture terminations do not prove that infinite loops are impossible for all inputs.

## E. Resume-safe numbers (with scope stated)

- 94/94 offline local tests passed (0 failed, 0 skipped).
- 3/3 explicitly labeled routing fixtures matched their expected tool sets; pooled precision and recall were 6/6 in that fixture set.
- Initial clarification was correct on 9/9 positive and 15/15 negative M3 fixture requests.
- Five existing fact-correction fixture scenarios passed; bounded recovery routes matched 7/7 fixture expectations and termination matched 4/4 boundary fixtures.

## F. Numbers that must not be used as general performance claims

- `100% routing` or `100% clarification` across all 24 scripted Cases or real phishing messages.
- A percentage reduction in unnecessary calls, or a claim that A1 used fewer tools than W0.
- A1 superiority in Case Success, Evidence Support, latency, or token cost from service-error-contaminated or unreviewed traces.
- A universal `zero infinite loops` guarantee, or a formal 12-Case held-out success rate.
