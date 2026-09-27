# Account and authentication implementation

Scope: Email/password for the overseas mobile App, using the
existing Product Backend identities, RS256 tokens, and device sessions.

Email delivery and deployment must be verified separately from local tests.
Platform verification currently covers Android; the iOS build remains unverified.

## Architecture and acceptance checklist

- [x] Canonical User email/status/verification/login timestamps; provider identities remain separate.
- [x] Signup creates email_unverified user, generic duplicate response, verification email/code, expiry/resend.
- [x] Email login validates password then status; only active users receive business sessions.
- [x] Password reset is purpose-bound, single-use, expiring and revokes every device session and pending expert MFA login challenge.
- [x] Account status enforced in Product API and Runtime API, including existing tokens.
- [x] IP/account rate limits, password hashing, no credential logs, production config validation.
- [x] Mobile secure storage, startup restoration, refresh single-flight and logout race handling.
- [x] Account deletion revokes sessions immediately, anonymizes authentication identity and tracks downstream erasure.

## Decisions

Email codes expire in 15 minutes, have a maximum of five attempts, and are stored
as purpose-bound HMAC digests. Resends invalidate preceding codes. Email reset
and registration responses do not disclose account existence. Email is normalized
case-insensitively without Gmail dot/plus rewriting. New email accounts must prove
mailbox ownership before obtaining a session. Existing email accounts must verify
on migration; invite accounts remain compatible for the internal test channel.
Account rate limits use the same normalized email as authentication, including
Unicode domain normalization, so equivalent spellings share one account bucket.

Historical external identity rows remain attached to their users for recovery
and audit. They no longer provide a sign-in or linking endpoint.

User.status is the account status source of truth (`active`, `email_unverified`,
`disabled`, `suspended`, `deleted`). Each request checks the active server-side
session AND account; RS256 claims alone are insufficient for revocation.

## Deletion and retention

Deletion immediately denies login/API access, revokes all sessions, clears email,
provider subjects/passwords, and creates a durable downstream erasure request.
Product clinical records, payment records, Runtime data and object storage require
separate processing. The API must report pending erasure, never claim complete
physical deletion on soft-delete. The repository currently defines no legal
retention schedule: production operations must approve categories/durations and
record exceptions before processing retained clinical/payment data. A completed
request requires acknowledgements from Product, Runtime and object storage.

## External configuration

SMTP host/port/TLS credentials and verified sender belong in deployment secrets.
Local substitutes do not prove live mailbox delivery.

## API and mobile flows

All paths below are under `/v1/auth`. Auth responses use `Cache-Control: private,
no-store`. Errors retain the existing error envelope and request ID. Body tokens
must never be placed in URLs or logs.

| Method / path | Behavior |
| --- | --- |
| POST `/register` | Email only -> generic 202 and an 8-digit verification code for eligible pending accounts. A legacy password field is accepted but ignored; no user-chosen credential is stored before proof. |
| POST `/verify-registration-code` | Email and code -> `code_valid` on success, without consuming the code, activating the account, or issuing a session. Invalid codes count toward the five-attempt limit. |
| POST `/verify-email` | After the code check, email, same unexpired code, chosen password, matching `confirm_password`, device ID -> consume proof and issue token pair. The backend requires confirmation for every request. |
| POST `/signup` (legacy compatibility) | Old email/password request -> generic 202; password remains provisional until mailbox proof and may be replaced at `/verify-email`. New clients use `/register` instead. |
| POST `/resend-verification` | Generic 202, minimum 60 seconds between sends. |
| POST `/login` | Email/password -> active account's token pair; legacy PBKDF2 passwords remain valid. |
| POST `/forgot-password` | Generic 202 for all emails. |
| POST `/reset-password` | Email, purpose-bound code, new password and matching `confirm_password` -> revoke all sessions. |
| POST `/change-password` | Active bearer session, current email password and matching new password confirmation -> revoke every device session; sign in again. |
| POST `/refresh` | Rotate opaque refresh token; reuse revokes family and device session. |
| POST `/logout` | Revoke authenticated device session. |
| POST `/logout-session` | Refresh-token possession revokes its device session, including previously rotated tokens; generic success. |
| GET `/me` | ID, email, verification, account status, providers and lifecycle timestamps. |
| DELETE `/me` | Revoke all sessions, anonymize authentication identifiers, create pending erasure request. |

The overseas mobile UI uses `/login` for login/registration/verification/reset and
`/account` for account details, password change, password recovery guidance, logout and confirmed
deletion. Existing GoRouter protection preserves a safe relative return path.
The internal test channel can retain its invite-only page using
`--dart-define=MOMCOZY_INTERNAL_INVITE_LOGIN=true`; this is not the consumer default.

A mailbox proof lets the owner choose a fresh password at verification time. The consumer App first requests a code with only an email, checks the code, then requires the new password twice. The pre-check is not a session or reservation: if the code expires or is exhausted before the final `/verify-email` call, the user must request another code.
Repeated registration cannot replace a pending account's password. Verification
also revokes any prior sessions, preventing account pre-hijacking. Duplicate
registration intentionally does not reveal account existence; the UI provides
sign-in, resend and reset alternatives.

## Session and failure behavior

RS256 access-token lifetime follows existing settings (default 15 minutes).
Refresh tokens are random, stored as hashes on the server, rotate on use and
expire after 30 days. The native App retains its existing atomic secure storage.
Startup checks `/me`; expired authentication attempts refresh, terminal failures
clear credentials, and temporary network failures retain the durable session for
retry. Retaining a local session during an outage does not authorize a server
request. Product API and Runtime both check current account/session validity.
Runtime fails closed with 503 if Product cannot verify the account.

Expert login, OTP verification and authenticator rotation lock the User row
before the MFA credential. OTP verification re-reads its challenge after acquiring
these locks; password reset or mailbox activation revokes pending challenges
along with established sessions.

Refresh requests share one in-flight operation. Session writes are serialized;
logout invalidates prior writers before clearing credentials. A late refresh or
login completion cannot republish credentials. Logout attempts server revocation
with the refresh token even if the access token has expired. Network/storage
failure is reported; it must not be described as verified server logout. User
asset caches and card exports are cleared with the native logout path.

This integration targets native Android/iOS. The current Dart network layer uses
`dart:io`; a browser client requires its own supported network flow and a
cookie-based session design before any production web credential persistence.

## Mail delivery and deployment preparation

For the proposed Resend SMTP integration and its pending real-service gates, see [`resend-auth-email-integration.md`](resend-auth-email-integration.md). No Resend key, DNS change, cloud deployment, or live mailbox acceptance is implied by local SMTP tests.

Copy variable names from `env/account-auth.env.example` into the existing secret
configuration. Supply one stable random `AUTH_EMAIL_TOKEN_KEY` with at least 32
bytes, SMTP STARTTLS host/port (587), authenticated sender/credentials. API and mail worker must share this key. SMTP must support
certificate-verified STARTTLS; implicit TLS port 465 is not implemented.

Run `python -m scripts.deliver_auth_emails` as a supervised process with the same
DB/secrets environment. Both local and test Compose definitions include `auth-email-worker` and force active-session enforcement for API requests.
A challenge and its encrypted `auth_email_deliveries` row commit together. The
encrypted content includes the challenge ID, digest and issuance timestamp.
The worker locks the account before the delivery using `FOR UPDATE SKIP LOCKED`,
checks that this challenge version is current and usable, and holds the account
lock through SMTP delivery. Resend/reset/deletion take the same account lock.
Superseded or consumed codes and legacy queued messages without version metadata
are cancelled and scrubbed before sending; other challenge purposes remain valid.
The worker retries bounded failures, drops expired messages, and clears ciphertext
after delivery/final failure. No additional database migration is needed for this
encrypted payload version; an unsent legacy code requires a fresh resend.
Delivery is at least once: a crash after SMTP acceptance may resend the same
code. Monitor pending age, expired/failed counts, and worker liveness; do not log
recipient addresses, codes or SMTP response text. Rotating the encryption key
invalidates outstanding codes and queued ciphertext; coordinate rotation.

Before an authorized release: back up the database; ensure verified sender and
mail delivery are working; apply migration `20260910_0017`; run API and mail
worker; and verify public endpoints. This migration preserves IDs/business
records, backfills canonical email, and marks historical email users unverified
because no mailbox proof existed. Their sessions are revoked. Announce this
verification requirement before rollout for existing users.
Invite identities remain usable for the internal test channel. Rollback does not
restore revoked sessions or falsely mark users verified.

## Retired external sign-in account recovery

The App and API no longer offer external sign-in or account linking. Existing
identity rows remain attached to their users; they are **not** deleted. A
previously verified, active account with no email password can use **Forgot your
password?** to request a reset code at its registered mailbox. Consuming that
purpose-bound, single-use code creates the email credential for the same user ID,
revokes old sessions, and preserves all business records. Duplicate registration
still returns the same generic response and cannot set a password without mailbox
proof. Suspended, deleted, and unverified external identities are not eligible.

Before removing a production login path, verify mail delivery and tell affected
users to complete password recovery. Check the number of active accounts without
an email identity and support users who no longer control their registered
mailbox via a separately verified support process; do not relink by email alone.

Read-only inventory before rollout:

```sql
SELECT count(*) AS external_only_accounts
FROM users AS u
WHERE u.status = 'active' AND u.email IS NOT NULL
  AND EXISTS (SELECT 1 FROM auth_identities AS i
              WHERE i.user_id = u.id AND i.provider = 'google')
  AND NOT EXISTS (SELECT 1 FROM auth_identities AS i
                  WHERE i.user_id = u.id AND i.provider = 'email');
```

## Verification evidence and remaining work

Local checks cover real PostgreSQL migration/schema comparison, concurrent
registration and legacy-account recovery, refresh reuse, logout/deletion with old
access and refresh tokens, transactional encrypted mail delivery/retries, native
secure storage, startup restore, late-write
logout races, English forms and confirmed deletion UI. See the account/auth
regression suites in Backend, Runtime and App. These are not live SMTP acceptance tests.

Operational work still required: project OAuth registrations and SMTP secrets,
verified sending domain, live device/mail round trips, and an authorized release.
No cloud deployment or real provider transaction was performed for this change.
Data erasure across clinical/business tables, Runtime stores and object storage
remains a durable request plus documented interface boundary, not a completed
physical erasure implementation. Define approved retention rules and the
acknowledgement workflow before processing requests. Future optional work:
provider unlinking, Apple sign-in, password/email change with reauthentication,
a device-session management screen, and automated provider security alerts.


### Local validation results (2026-09-10)

- Backend full regression after the account review fixes: 783 passed, 2 skipped.
  The skipped tests require separately configured real Redis and LiveKit services.
  Twelve added regression cases cover password-reset MFA invalidation, actual
  PostgreSQL login/OTP deadlock reproduction, canonical email account limiting,
  and version-checked mail retries with concurrent resend/delivery. The original
  five reproductions failed before the fixes and all added cases now pass.
  Backend Ruff and Mypy (261 source files) passed.
- Runtime account/session adapter and authentication/API regressions: 62 passed;
  targeted Ruff and Mypy passed.
- Flutter auth/storage/routes/navigation/native-config regressions: 61 passed;
  targeted `flutter analyze` and backend-contract validation passed.
- Fresh PostgreSQL upgrade from `20260909_0016` to `20260910_0017` preserved the
  seeded account IDs and marked only the email account unverified; three auth
  lifecycle tables were created. `alembic check` found no schema drift.
- Native Android local debug App Bundle compiled successfully. The machine's
  Maven-provided AAPT2 daemon failed to start; the verification retry used the
  installed SDK 36.1.0 AAPT2 through a process-local Gradle property. No project
  AAPT2 path or shared build setting was changed. The artifact was not published.
  Existing Kotlin plugin future-compatibility warnings remain.

No iOS build or live SMTP inbox acceptance was verified.
The checked implementation items above must not be interpreted as production
provider credentials or operational acceptance being complete.
