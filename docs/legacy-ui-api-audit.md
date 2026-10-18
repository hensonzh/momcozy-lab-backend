# Legacy UI API audit (current chat-first App)

Reviewed: 2026-10-18. Scope: the `app/lib/` shipped Dart source, the Agent
runtime HTTP client, and the Product Backend v1 OpenAPI. This is a **static
consumer audit**, not production traffic or old-build retirement evidence.

| Product Backend surface | Current App / Agent runtime | Disposition |
|---|---|---|
| `/v1/plans*` (10 operations) | No current App or Agent HTTP caller. Current personal schedule uses `/v1/schedule`; the historical plan/task domain remains a reader for schedule timeline data. | Move HTTP adapter and wire schemas to `plans/legacy_*`. Keep v1 behavior, owner scope and idempotency for older clients. Do not add new Flutter dependencies. |
| `/v1/support/tickets*` (3 operations) | No current App or Agent HTTP caller. Other released clients have not been inventoried. | Isolate in `support/legacy_*` and classify as deprecated compatibility, without changing its request or response contract. |
| Auth/session, `/v1/profile/me`, `/v1/babies`, `/v1/records*`, `/v1/schedule*`, files, devices, notifications and voice | Used by the current flow, reachable compatibility deep links, or Agent/internal paths. | Retain; absence from the home screen is not evidence of an unused API. |

`app/scripts/tests/test_current_app_api_dependencies.py` guards against the
current App importing the two compatibility-only HTTP surfaces. Backend
OpenAPI/HTTP tests verify that legacy clients still get the same methods,
authentication and owner-scoped behavior. The live-to-candidate OpenAPI
compatibility checker reports no breaking operations for this refactor.

**Release boundary:** `docs/api-retirement-runbook.md` records historical old
client traffic and unexpired sessions at its observation date. Those figures
are not a present-day usage measurement. Removal from a live v1 host requires
a completed released-client inventory, the mandated observation window and
retirement review; `scripts/check_api_retirement_readiness.py` and the live
OpenAPI CI gate remain intact. No database table, stored plan/task data, Agent
contract, host configuration or deployed API was removed by this change.
