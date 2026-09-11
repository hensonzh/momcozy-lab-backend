# Notification module

Acceptance scope: local implementation and verification. The user confirmed on
2026-09-10 that Firebase/FCM and APNs configuration is not yet prepared. Live push
delivery and distribution acceptance must remain explicitly unverified.

## Existing architecture and implementation plan

Extend `modules/notifications` and Flutter `features/notifications`. Preserve the
existing inbox/read/archive contract and ChangeNotifier/API Runtime/GoRouter
architecture. The existing Android pump foreground/local notifications remain a
device feature; permission checks will share the application permission policy.

`care_service_events` already records transactional business milestones. A
notification consumer projects these events into the existing notification
table; a durable PostgreSQL worker follows the existing care worker pattern.
Business modules emit events and never import a push provider.

The existing `notifications` table becomes the notification/task source of truth:
read state remains independent of send state. Add trigger/expiry/cancellation,
resource/route and durable deduplication fields. Future reminders are excluded
from the inbox until due. Provider acceptance sets `sent_at`, never `delivered_at`.
Per-installation delivery rows prevent a successful device from being retried
because a different device failed. External delivery is at least once across a
crash after provider acceptance; stable notification IDs and platform collapse
identifiers limit duplicate presentation. Do not promise exactly-once push.

Push installations have a persistent random ID and secret, encrypted token,
unique token digest, binding generation, current authenticated session, permission,
locale and last-observed revision/time. Logout unbinds the session, retaining the
installation/token. The worker rechecks account/session/resource ownership before
each send. A payload contains only an opaque notification ID and binding ID;
lock-screen text is generic. The authenticated notification API resolves routes.

Service preferences are separate categories (appointments, consultations, expert
feedback, service updates); marketing is separate and disabled. Background task
creation requires a current permission check and successful device registration.
Appointment creation can proceed when reminders are unavailable, with that state
visible. Recovery only enables future, valid reminders, never expired messages.

Cold/background/foreground clicks feed the existing router through a notification
resolver. Pending IDs survive authentication; fetching by current user prevents
account-switch leaks. Opening marks the backend notification read and refreshes
the authoritative unread count. Missing/deleted resources get an explicit fallback.

OS permission is authoritative on the device. Check at startup, foreground,
settings entry/return and before each notification action. Startup never prompts.
First use explains value before requesting authorization; denial opens settings
on a later explicit action. A server cannot instantly observe an OS setting change
while the App is stopped; reported permission is reconciled on those checkpoints.

## Completion audit — local scope, 2026-09-10

The checks below mean implementation and local automated verification. Provider
responses and mobile lifecycle events use controlled adapters in tests; they do
not establish delivery on a physical device. See the remaining acceptance list.

- [x] Current architecture, providers, auth, events, jobs, router and lifecycle inspected.
- [x] Unified permission state and first-use education; never prompt at launch.
- [x] Authorized operations do not request again; denial/settings recovery works.
- [x] Revocation is rechecked at every required lifecycle/operation boundary.
- [x] Durable notification state, scheduling, resource references and migration.
- [x] Transactional event projection for all six required business notification types.
- [x] Appointment creation/change/cancellation and overdue reminders reconcile.
- [x] FCM adapter, bounded retry, invalid tokens, per-device delivery and privacy.
- [x] Token registration/refresh/reinstall/account switch/logout/multiple devices.
- [x] Category preferences and explicit permission-gated reminder enabling.
- [x] Inbox pagination, unread count/badge, single/all read, loading/empty/error.
- [x] Structured route resolution and resource ownership checks.
- [x] Foreground/background/cold start and logged-out pending notification click.
- [x] Offline, expired session, removed resources and failed scheduler recovery.
- [x] Backend/PostgreSQL/API/provider regression tests and schema verification.
- [x] Flutter controller/widget/platform configuration and contract verification.
- [x] Final architecture, state flows, tables, APIs, UI, trigger map, test evidence
      and remaining external acceptance documented.

The implementation and operations reference is
[notification-operations.md](notification-operations.md). The Flutter integration
reference is [App notification module](../../app/docs/notification-module.md).

## Verification evidence

| Check | Result | Scope |
|---|---|---|
| Complete backend regression | **810 passed, 2 skipped** | Real local PostgreSQL enabled; external Redis and LiveKit checks skipped |
| Notification backend regressions | **27 passed**, included above | Provider 6, installation 7, inbox 1, lifecycle 10, API 1, business flow 1, migration 1 |
| Migration `0017 → 0018` | Passed | Existing read/content preserved, old-event receipts seeded, model/schema parity checked, downgrade and re-upgrade passed |
| Ruff / mypy | Passed | Backend app/tests/scripts lint; app/scripts type checking, 267 source files |
| Complete Flutter regression | **957 passed** | Includes 26 notification tests and the target-agent-conversation navigation regression |
| Flutter analyzer | Passed | Full App, no issues |
| API contract validation | Passed | 148 Product paths, 21 Runtime paths, 4 smoke-flow contracts; generated Product OpenAPI synchronized to App |
| Local/test Compose configuration | Passed | Configuration validation only; no deployment |
| Android local/debug arm64 APK | Passed | Package `com.momcozymai.app.flutterpoc.local`, version `1.0.0+57`; development artifact only |
| iOS native Swift / project configuration | Passed | All Runner Swift files typechecked with real Flutter/UIKit frameworks for iOS 15 simulator; plist, entitlements and Xcode project lint passed |
| Complete iOS simulator build | **Not completed** | SwiftPM Firebase SDK fetch stalled; an official source download retry timed out. No iOS package acceptance claimed |

The PostgreSQL tests cover concurrent projection/delivery, permission-before-task
gates, future-only recovery, rescheduling/cancellation, bounded per-device retries,
invalid tokens, logout during retry, resource/account ownership, signed-plan
publication and all six business notification types. The API test uses the real
FastAPI application, session and database boundary. The migration test uses its
own disposable database.

Flutter tests cover first-use education, consent/denial, no repeat permission
prompt, settings restoration, token refresh, missing client/server provider,
re-login revision ordering, late responses after logout, current-binding banners,
cold-start and expired-session pending clicks, unsafe routes, inbox paging/read-all
and backend-derived reminder state. These are automated tests, not a claim that
OS-delivered push or killed-app behavior has been tested on hardware.

Local evidence logs are `/tmp/momcozy-notification-backend-complete.log`,
`/tmp/momcozy-notification-flutter-full-final.log`,
`/tmp/momcozy-notification-flutter-analyze-final.log`,
`/tmp/momcozy-notification-android-build-final.log`, and
`/tmp/momcozy-notification-ios-typecheck-final.log`. They are temporary local
artifacts. The Android APK is at
`app/build/app/outputs/flutter-apk/app-local-debug.apk` relative to the workspace;
SHA-256 `cbc8d36b1207804027c7cd22e6e90b8a9bb2b3ef2115a6a03e836c0d53577156`.

## Remaining acceptance and later additions

September 11 review follow-up: the App now starts iOS remote registration before
waiting for APNs, and uses the account/login generation rather than runtime
object identity for notification binding. Same-session client replacements no
longer detach an installation, avoiding the pending-send disablement reported
when switching babies. Seven new App regressions protect these paths; **964
Flutter tests passed** and full analyzer is clean. See the
[App review-fix record](../../app/docs/notification-module.md#review-fixes--2026-09-11).
No backend implementation or schema changed in this follow-up; the backend
results above remain the September 10 baseline. No mobile package was rebuilt.

- [ ] Complete iOS simulator packaging after the official Firebase dependency
      download is available. The selected SDK requires **iOS 15+**; notification
      settings use the iOS 16-specific entry point only on iOS 16 or later.
- [ ] Configure the real Firebase/FCM project and APNs credentials/signing, then
      verify Android and iOS physical-device delivery and lifecycle behavior.
- [ ] Deploy the migration/API/worker and verify target-environment supervision,
      provider connectivity, quotas and retry behavior. This task did not deploy.

Additional locales, a dedicated delivery metrics/alerting dashboard and an
operator replay UI remain future additions. Marketing delivery remains disabled.
Provider acceptance cannot prove delivery; a crash after provider acceptance can
still result in a duplicate push. See the operations reference for these limits
and for private configuration locations. No live provider credentials are needed
for the completed local tests.

## Primary platform references

- [FCM Flutter setup](https://firebase.google.com/docs/cloud-messaging/flutter/get-started)
- [FCM Flutter receive/click lifecycle](https://firebase.google.com/docs/cloud-messaging/flutter/receive-messages)
- [FCM token lifecycle](https://firebase.google.com/docs/cloud-messaging/manage-tokens)
- [FCM HTTP v1 authentication](https://firebase.google.com/docs/cloud-messaging/send/v1-api)
- [Android notification permission](https://developer.android.com/develop/ui/compose/notifications/notification-permission)
