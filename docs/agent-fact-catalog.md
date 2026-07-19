# Agent Fact Catalog

This catalog is the single source of truth for reusable information collected by the pregnancy-plan, hospital-bag, and birth-plan forms. Runtime code lives in `app/modules/agent_runtime/facts/catalog.py`.

## Lifecycle

1. Run creation persists the user message, run, and idempotent fact-extraction job in the same database transaction. A post-commit Redis signal only wakes the worker; PostgreSQL remains the source of truth.
2. The main Agent run and the fact worker execute concurrently. Fact extraction uses a separate session and bounded pool, so provider latency or failure never delays or fails the main run.
3. A no-tool `gpt-5.4-nano` extractor reads the current user message, up to five recent dialogue messages, and the candidate-eligible catalog. It receives no existing stored facts and emits strict JSON. Deterministic validation binds each value to an unambiguous source clause and rejects third-party, previous-pregnancy, uncertain, or restricted-health content even when the model labels it incorrectly.
4. The durable job uses leased claims, fencing tokens, bounded retries, and dead-lettering. Candidate payloads contain only canonical key/value/subject data; raw messages and evidence are not copied into the job or fact row, and the transient candidate payload is scrubbed when the job completes.
5. Extracted candidates are applied only after the source run reaches a terminal state. `waiting_for_confirmation` remains non-terminal because the same run can resume, so the current run never races its own extracted result. Candidates become visible from the next turn or next form. Sanitized candidates waiting on a nonterminal source run are scrubbed after 14 days and the extraction job is cancelled.
6. Verified form submissions and conversation candidates occupy separate rows. Resolution always prefers `verified`; conversation candidates only fill missing values and expire by catalog TTL.
7. Form creation merges live profile/business/workflow data first, then verified facts, and uses candidates only for remaining blanks.

## Consent and Management

- The existing Agent memory setting is the consent gate. Opt-out prevents enqueue, extraction, application, runtime reads, and form prefill, and cancels pending extraction jobs in the same settings transaction; workers also re-check consent before extraction and before apply.
- `GET /v1/agent/facts` lists the current user's active facts even while capture is disabled, so retained data stays visible and manageable.
- `DELETE /v1/agent/facts/{fact_id}` and `DELETE /v1/agent/facts` tombstone rows, scrub values and internal source identifiers, write value-free audit records, and cancel pending extraction jobs for that owner so an in-flight older message cannot repopulate deleted data.
- Conversation candidates have bounded TTLs and are scrubbed when expired, promoted by a verified value, or explicitly deleted. Pending job payloads are scrubbed when opt-out is detected, a job is dead-lettered, or its nonterminal-source retention deadline elapses before application.
- IVF, prior-birth history, medical notes, and doctor notes are `restricted_health`: verified forms may store them, but background conversation extraction never requests or retains them.

## Collection Scenarios

| Scenario | Form ID | Collected field groups |
|---|---|---|
| Pregnancy plan | `birth_journey_basic_info_intake` | gestation, IVF, fetus count, age, birth history/path, location, hospital, medical and doctor notes |
| Hospital bag | `hospital_bag_intake` | gestation, birth history/path, fetus count, feeding, return to work, support, worries |
| Birth plan | `birth_plan_card_intake` | gestation, birth setting/path, support, communication, labor, interventions, pain relief, newborn and emergency preferences |

## Canonical Fields

| Namespace | Keys | Conversation candidates |
|---|---|---|
| Profile | `profile.age` | Allowed with explicit self attribution |
| Pregnancy basics | `pregnancy.due_date_or_week`, `pregnancy.fetus_count`, `pregnancy.first_birth` | Allowed with explicit current-pregnancy attribution and safe structured values; gestation TTL is 14 days |
| Restricted pregnancy history | `pregnancy.ivf`, `pregnancy.prior_birth_history` | Never |
| Pregnancy setting | `pregnancy.birth_path` | Allowed as a closed enum |
| Open pregnancy location | `pregnancy.city_or_country`, `pregnancy.birth_setting` | Verified forms only |
| Restricted pregnancy care | `pregnancy.medical_notes`, `pregnancy.doctor_notes` | Never |
| Pregnancy feeding | `pregnancy.feeding_intention` | Allowed as a closed enum |
| Open pregnancy preferences | `pregnancy.return_to_work_timing`, `pregnancy.support_person`, `pregnancy.top_worries` | Verified forms only |
| Birth-plan communication | `birth_plan.top_priorities`, `birth_plan.communication_preferences`, `birth_plan.priority_notes` | Verified forms only |
| Birth-plan labor | `birth_plan.labor_preferences`, `birth_plan.intervention_preferences`, `birth_plan.pain_relief_preferences`, `birth_plan.pain_relief_notes` | Verified forms only |
| Birth-plan newborn/change | `birth_plan.emergency_authorization` | Allowed as a closed enum; other newborn/change free text is verified forms only |

## Adding A Field

Update the catalog definition and explicit form mapping together, make sensitivity/candidate eligibility/TTL explicit, and bump `FACT_CATALOG_VERSION` when extraction semantics change. Add deterministic mapping/merge tests plus observed cases in `fixtures/agent_eval_cases/user_fact_extraction_seed.json` for subject attribution, corrections, uncertainty, restricted fields, and cross-turn prefill.
