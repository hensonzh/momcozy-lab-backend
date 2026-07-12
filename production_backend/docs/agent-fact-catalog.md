# Agent Fact Catalog

This catalog is the single source of truth for reusable information collected by the pregnancy-plan, hospital-bag, and birth-plan forms. Runtime code lives in `app/modules/agent_runtime/facts/catalog.py`.

## Lifecycle

1. A no-tool structured extractor reads the current user message, up to five recent dialogue messages, current facts, and this catalog.
2. The extractor uses `gpt-5.4-nano` and emits strict JSON. Only facts marked `explicit` are eligible for storage.
3. The deterministic service validates key, type, enum, owner, source time, and source priority before writing `user_facts`.
4. Verified form submissions write the same canonical facts with higher priority than conversation extraction from the same message.
5. Form creation reads `user_facts` and maps canonical values into that form's `default_values`.
6. Conversation facts are captured after the current run result is finalized, so they become visible from the next turn.

## Collection Scenarios

| Scenario | Form ID | Collected field groups |
|---|---|---|
| Pregnancy plan | `birth_journey_basic_info_intake` | gestation, IVF, fetus count, age, birth history/path, location, hospital, medical and doctor notes |
| Hospital bag | `hospital_bag_intake` | gestation, birth history/path, fetus count, feeding, return to work, support, worries |
| Birth plan | `birth_plan_card_intake` | gestation, birth setting/path, support, communication, labor, interventions, pain relief, newborn and emergency preferences |

## Canonical Fields

| Namespace | Keys |
|---|---|
| Profile | `profile.age` |
| Pregnancy basics | `pregnancy.due_date_or_week`, `pregnancy.ivf`, `pregnancy.fetus_count`, `pregnancy.first_birth`, `pregnancy.prior_birth_history` |
| Pregnancy setting | `pregnancy.birth_path`, `pregnancy.city_or_country`, `pregnancy.birth_setting` |
| Pregnancy care | `pregnancy.medical_notes`, `pregnancy.doctor_notes`, `pregnancy.feeding_intention`, `pregnancy.return_to_work_timing`, `pregnancy.support_person`, `pregnancy.top_worries` |
| Birth-plan communication | `birth_plan.top_priorities`, `birth_plan.communication_preferences`, `birth_plan.priority_notes` |
| Birth-plan labor | `birth_plan.labor_preferences`, `birth_plan.intervention_preferences`, `birth_plan.pain_relief_preferences`, `birth_plan.pain_relief_notes` |
| Birth-plan newborn/change | `birth_plan.baby_after_birth_preferences`, `birth_plan.if_plans_change`, `birth_plan.emergency_authorization`, `birth_plan.hospital_questions_focus` |

## Adding A Field

Update the catalog definition and explicit form mapping together, bump `FACT_CATALOG_VERSION` when extraction semantics change, then add deterministic mapping/merge tests and model eval cases for subject attribution, corrections, uncertainty, and cross-turn prefill.
