# MomCozy API Surface Catalog

This catalog is generated from `docs/openapi.generated.json`.
The source of truth is each FastAPI route's OpenAPI extension metadata.

| Surface | Meaning |
|---|---|
| `public_app_api` | Stable API called by the Flutter app. |
| `runtime_stream_api` | App-facing streaming or realtime transport contract. |
| `internal_service_api` | Backend service or worker API; not a direct app contract. |
| `admin_ops_api` | Operations, replay, eval, or support tooling API. |
| `infra_probe_api` | Health, readiness, metrics, or infrastructure probe API. |
| `deprecated_api` | Still present but scheduled for removal. |

## public_app_api

| Method | Path | Owner | Clients | Stability | Summary |
|---|---|---|---|---|---|
| GET | `/v1/assets` | product-assets | flutter | stable | List Product Assets |
| GET | `/v1/assets/{asset_id}` | product-assets | flutter | stable | Get Product Asset |
| POST | `/v1/auth/forgot-password` | auth | flutter | stable | Forgot Password |
| POST | `/v1/auth/google` | auth | flutter | stable | Google Login |
| POST | `/v1/auth/google/link` | auth | flutter | stable | Link Google |
| POST | `/v1/auth/invite-login` | auth | flutter | stable | Invite Login |
| POST | `/v1/auth/login` | auth | flutter | stable | Login |
| POST | `/v1/auth/logout` | auth | flutter | stable | Logout |
| POST | `/v1/auth/logout-session` | auth | flutter | stable | Logout Session |
| DELETE | `/v1/auth/me` | auth | flutter | stable | Delete Account |
| GET | `/v1/auth/me` | auth | flutter | stable | Account Me |
| POST | `/v1/auth/refresh` | auth | flutter | stable | Refresh |
| POST | `/v1/auth/register` | auth | flutter | stable | Register |
| POST | `/v1/auth/resend-verification` | auth | flutter | stable | Resend Verification |
| POST | `/v1/auth/reset-password` | auth | flutter | stable | Reset Password |
| POST | `/v1/auth/signup` | auth | flutter | stable | Signup |
| POST | `/v1/auth/verify-email` | auth | flutter | stable | Verify Email |
| GET | `/v1/babies` | baby | flutter | stable | List Profiles |
| POST | `/v1/babies` | baby | flutter | stable | Create |
| PUT | `/v1/babies/{baby_id}` | baby | flutter | stable | Update |
| GET | `/v1/babies/{baby_id}/records` | baby | flutter | stable | List Records |
| POST | `/v1/babies/{baby_id}/records` | baby | flutter | stable | Create |
| POST | `/v1/babies/{baby_id}/records/batch` | baby | flutter | stable | Create Batch |
| GET | `/v1/babies/{baby_id}/records/latest-growth` | baby | flutter | stable | Latest Growth |
| DELETE | `/v1/babies/{baby_id}/records/{record_id}` | baby | flutter | stable | Delete |
| PUT | `/v1/babies/{baby_id}/records/{record_id}` | baby | flutter | stable | Update |
| POST | `/v1/babies/{baby_id}/records/{record_id}/restore` | baby | flutter | stable | Restore |
| GET | `/v1/care/appointments/{appointment_id}` | care | flutter | stable | Get Appointment |
| POST | `/v1/care/appointments/{appointment_id}/cancel` | care | flutter | stable | Cancel Appointment |
| POST | `/v1/care/appointments/{appointment_id}/confirm` | care | flutter | stable | Confirm Appointment |
| GET | `/v1/care/appointments/{appointment_id}/documentation` | care | flutter, ibclc | stable | Documentation |
| GET | `/v1/care/appointments/{appointment_id}/intake` | care | flutter | stable | Intake Context |
| PUT | `/v1/care/appointments/{appointment_id}/intake` | care | flutter | stable | Save Intake |
| POST | `/v1/care/appointments/{appointment_id}/location-check` | care | flutter, ibclc | stable | Check Location |
| PUT | `/v1/care/appointments/{appointment_id}/note` | care | flutter, ibclc | stable | Save Note |
| POST | `/v1/care/appointments/{appointment_id}/note/amend` | care | flutter, ibclc | stable | Amend Note |
| POST | `/v1/care/appointments/{appointment_id}/note/sign` | care | flutter, ibclc | stable | Sign Note |
| GET | `/v1/care/appointments/{appointment_id}/notes/{note_id}` | care | flutter, ibclc | stable | Note Revision |
| PUT | `/v1/care/appointments/{appointment_id}/plan` | care | flutter, ibclc | stable | Save Plan |
| POST | `/v1/care/appointments/{appointment_id}/plan/publish` | care | flutter, ibclc | stable | Publish Plan |
| GET | `/v1/care/appointments/{appointment_id}/room` | care | flutter, ibclc | stable | Room Context |
| POST | `/v1/care/appointments/{appointment_id}/room` | care | flutter, ibclc | stable | Prepare Room |
| POST | `/v1/care/appointments/{appointment_id}/room/end` | care | flutter, ibclc | stable | End Consultation |
| POST | `/v1/care/appointments/{appointment_id}/room/join` | care | flutter, ibclc | stable | Join Room |
| PUT | `/v1/care/appointments/{appointment_id}/room/presence` | care | flutter, ibclc | stable | Room Presence |
| POST | `/v1/care/appointments/{appointment_id}/room/start` | care | flutter, ibclc | stable | Start Consultation |
| GET | `/v1/care/appointments/{appointment_id}/summary` | care | flutter, ibclc | stable | Patient Summary |
| GET | `/v1/care/catalog` | care | flutter | stable | Get Catalog |
| POST | `/v1/care/eligibility` | care | flutter | stable | Check Eligibility |
| GET | `/v1/care/episodes/{episode_id}/availability` | care | flutter | stable | Booking Availability |
| GET | `/v1/care/episodes/{episode_id}/booking` | care | flutter | stable | Booking Context |
| POST | `/v1/care/episodes/{episode_id}/booking-eligibility` | care | flutter | stable | Booking Precheck |
| GET | `/v1/care/episodes/{episode_id}/consents` | care | flutter | stable | Get Consents |
| POST | `/v1/care/episodes/{episode_id}/consents` | care | flutter | stable | Set Consent |
| GET | `/v1/care/episodes/{episode_id}/conversations` | reports | flutter | stable | Conversations |
| PUT | `/v1/care/episodes/{episode_id}/conversations/{thread_id}` | reports | flutter | stable | Link |
| POST | `/v1/care/episodes/{episode_id}/holds` | care | flutter | stable | Hold Appointment |
| POST | `/v1/care/orders` | care | flutter | stable | Create Order |
| GET | `/v1/care/orders/{order_id}` | care | flutter | stable | Read Order |
| POST | `/v1/care/orders/{order_id}/checkout` | care | flutter | stable | Create Checkout |
| POST | `/v1/care/orders/{order_id}/reconcile` | care | flutter | stable | Reconcile Checkout |
| POST | `/v1/care/orders/{order_id}/sandbox-payment` | care | flutter | stable | Simulate Payment |
| GET | `/v1/care/overview` | care | flutter | stable | Get Overview |
| PUT | `/v1/care/plan-publications/{publication_id}/tasks/{source_key}` | care | flutter, ibclc | stable | Update Task |
| POST | `/v1/care/stripe/webhook` | care | flutter | stable | Stripe Webhook |
| GET | `/v1/devices/pump-energy-target` | devices | flutter | stable | Get Pump Energy Target |
| POST | `/v1/devices/pump-health` | devices | flutter | stable | Create Pump Health |
| GET | `/v1/devices/pump-health/latest` | devices | flutter | stable | Get Latest Pump Health |
| GET | `/v1/devices/pump-telemetry` | devices | flutter | stable | List Pump Telemetry |
| POST | `/v1/devices/pump-telemetry` | devices | flutter | stable | Create Pump Telemetry |
| POST | `/v1/devices/pump-threshold` | devices | flutter | stable | Create Pump Threshold |
| GET | `/v1/devices/pump-threshold/latest` | devices | flutter | stable | Get Latest Pump Threshold |
| POST | `/v1/devices/pump-workstate` | devices | flutter | stable | Create Pump Workstate |
| GET | `/v1/devices/pump-workstate/latest` | devices | flutter | stable | Get Latest Pump Workstate |
| GET | `/v1/devices/pumps` | devices | flutter | stable | List Pump Devices |
| PUT | `/v1/devices/pumps/{device_id}` | devices | flutter | stable | Upsert Pump Device |
| GET | `/v1/files` | files | flutter | stable | List Files |
| POST | `/v1/files/upload` | files | flutter | stable | Upload File |
| DELETE | `/v1/files/{file_id}` | files | flutter | stable | Delete File |
| GET | `/v1/files/{file_id}` | files | flutter | stable | Get File |
| GET | `/v1/files/{file_id}/content` | files | flutter | stable | Get File Content |
| GET | `/v1/ibclc/appointments` | ibclc | ibclc | stable | Appointments |
| GET | `/v1/ibclc/appointments/{appointment_id}/intake` | care | flutter | stable | Expert Intake |
| POST | `/v1/ibclc/auth/login` | auth | ibclc | stable | Begin Workbench Login |
| POST | `/v1/ibclc/auth/verify` | auth | ibclc | stable | Verify Workbench Login |
| GET | `/v1/ibclc/calendar` | ibclc | ibclc | stable | Calendar |
| GET | `/v1/ibclc/clients` | ibclc | ibclc | stable | Clients |
| GET | `/v1/ibclc/clients/{patient_ref}` | ibclc | ibclc | stable | Client Detail |
| GET | `/v1/ibclc/episodes/{episode_id}/reports` | care-reports | ibclc | stable | Read |
| POST | `/v1/ibclc/episodes/{episode_id}/reports` | care-reports | ibclc | stable | Generate |
| GET | `/v1/ibclc/episodes/{episode_id}/reports/history` | care-reports | ibclc | stable | History |
| GET | `/v1/ibclc/followups` | ibclc | ibclc | stable | Followups |
| GET | `/v1/ibclc/me` | ibclc | ibclc | stable | Identity |
| GET | `/v1/ibclc/reminders` | ibclc | ibclc | stable | Reminders |
| PUT | `/v1/ibclc/reminders/{event_id}/read` | ibclc | ibclc | stable | Read Reminder |
| POST | `/v1/ibclc/reports/{report_id}/reviews` | care-reports | ibclc | stable | Review |
| GET | `/v1/lactation/records` | lactation | flutter | stable | List Records |
| POST | `/v1/lactation/records` | lactation | flutter | stable | Create Record |
| DELETE | `/v1/lactation/records/{record_id}` | lactation | flutter | stable | Delete Record |
| PUT | `/v1/lactation/records/{record_id}` | lactation | flutter | stable | Update Record |
| POST | `/v1/lactation/records/{record_id}/restore` | lactation | flutter | stable | Restore Record |
| GET | `/v1/model-assets/{token}` | files | openai-responses | stable | Get Agent Model Asset Opaque bearer capability used only for model input fetches. |
| GET | `/v1/mother/diary` | mother | flutter | stable | List Diary |
| PUT | `/v1/mother/diary/{entry_date}` | mother | flutter | stable | Save Diary |
| GET | `/v1/notifications` | notifications | flutter | stable | List Notifications |
| GET | `/v1/notifications/appointments/{appointment_id}/reminder` | notifications | flutter | stable | Read Appointment Reminder |
| PUT | `/v1/notifications/appointments/{appointment_id}/reminder` | notifications | flutter | stable | Update Appointment Reminder |
| POST | `/v1/notifications/installations` | notifications | flutter | stable | Register Push Installation |
| POST | `/v1/notifications/installations/{installation_id}/detach` | notifications | flutter | stable | Detach Push Installation |
| GET | `/v1/notifications/preferences` | notifications | flutter | stable | Notification Preferences |
| PATCH | `/v1/notifications/preferences/{category}` | notifications | flutter | stable | Update Notification Preference |
| POST | `/v1/notifications/read-all` | notifications | flutter | stable | Mark All Notifications Read |
| DELETE | `/v1/notifications/{notification_id}` | notifications | flutter | stable | Archive Notification |
| POST | `/v1/notifications/{notification_id}/open` | notifications | flutter | stable | Open Notification |
| PATCH | `/v1/notifications/{notification_id}/read` | notifications | flutter | stable | Set Notification Read State |
| GET | `/v1/profile/lactation` | profiles | flutter | stable | Get My Maternal Lactation Profile |
| PATCH | `/v1/profile/lactation` | profiles | flutter | stable | Update My Maternal Lactation Profile |
| GET | `/v1/profile/me` | profiles | flutter | stable | Get My Profile |
| PATCH | `/v1/profile/me` | profiles | flutter | stable | Update My Profile |
| GET | `/v1/profile/me-experience` | profiles | flutter | stable | Read Me |
| PUT | `/v1/profile/me-experience/concerns/{concern_id}` | profiles | flutter | stable | Put Concern |
| PUT | `/v1/profile/me-experience/order` | profiles | flutter | stable | Put Order |
| PATCH | `/v1/profile/me-experience/profile` | profiles | flutter | stable | Update Profile |
| PUT | `/v1/profile/me-experience/records/{record_id}` | profiles | flutter | stable | Put Record |
| GET | `/v1/records/feeding` | records | flutter | stable | List Feedings |
| POST | `/v1/records/feeding` | records | flutter | stable | Create Feeding |
| DELETE | `/v1/records/feeding/{record_id}` | records | flutter | stable | Delete Feeding |
| GET | `/v1/records/growth` | records | flutter | stable | List Growth |
| POST | `/v1/records/growth` | records | flutter | stable | Create Growth |
| DELETE | `/v1/records/growth/{record_id}` | records | flutter | stable | Delete Growth |
| PATCH | `/v1/records/growth/{record_id}` | records | flutter | stable | Update Growth |
| GET | `/v1/records/milk-trends` | records | flutter | stable | Get Milk Trends |
| GET | `/v1/records/pumping` | records | flutter | stable | List Pumpings |
| POST | `/v1/records/pumping` | records | flutter | stable | Create Pumping |
| DELETE | `/v1/records/pumping/{record_id}` | records | flutter | stable | Delete Pumping |
| GET | `/v1/schedule` | schedule | flutter, agent | stable | Read Schedule |
| POST | `/v1/schedule/personal` | schedule | flutter, agent | stable | Create Personal |
| DELETE | `/v1/schedule/personal/{task_id}` | schedule | flutter, agent | stable | Delete Personal |
| PATCH | `/v1/schedule/personal/{task_id}` | schedule | flutter, agent | stable | Update Personal |
| POST | `/v1/speech/transcribe-chunk` | voice | flutter | stable | Transcribe Speech Chunk |
| GET | `/v1/support/tickets` | support | flutter | stable | List Support Tickets |
| POST | `/v1/support/tickets` | support | flutter | stable | Create Support Ticket |
| GET | `/v1/support/tickets/{ticket_id}` | support | flutter | stable | Get Support Ticket |

## runtime_stream_api

| Method | Path | Owner | Clients | Stability | Summary |
|---|---|---|---|---|---|
| GET | `/v1/files/{file_id}/vision/events/stream` | files | flutter | stable | Stream File Vision Events |
| GET | `/v1/realtime-voice-stream` | voice | flutter | stable | Realtime Voice Stream |

## internal_service_api

| Method | Path | Owner | Clients | Stability | Summary |
|---|---|---|---|---|---|
| GET | `/.well-known/jwks.json` | auth | agent-runtime | stable | Jwks |
| POST | `/v1/internal/agent/actions/diary.entry/apply` | diary | agent-runtime | stable | Apply Agent Diary Action |
| POST | `/v1/internal/agent/actions/lactation.record/apply` | records | agent-runtime | stable | Apply Agent Lactation Record |
| POST | `/v1/internal/agent/actions/plans/apply` | plans | agent-runtime | stable | Apply Agent Plans Action |
| POST | `/v1/internal/agent/actions/profile.update/apply` | profiles | agent-runtime | stable | Apply Agent Profile Update |
| GET | `/v1/internal/agent/diary` | diary | agent-runtime | stable | Read Agent Diary |
| POST | `/v1/internal/agent/files/resolve` | files | agent-runtime | stable | Resolve Agent File |
| GET | `/v1/internal/agent/lactation/milk-analysis-snapshot` | records | agent-runtime | stable | Read Agent Milk Analysis Snapshot |
| GET | `/v1/internal/agent/plans/calendar` | plans | agent-runtime | stable | Read Agent Plan Calendar |
| GET | `/v1/internal/agent/plans/current` | plans | agent-runtime | stable | Read Agent Current Plans |
| GET | `/v1/internal/agent/plans/{plan_id}` | plans | agent-runtime | stable | Read Agent Plan Detail |
| GET | `/v1/internal/agent/profile` | profiles | agent-runtime | stable | Read Agent Profile |
| GET | `/v1/internal/agent/schedule-timeline` | plans | agent-runtime | stable | Read Agent Schedule Timeline |

## admin_ops_api

| Method | Path | Owner | Clients | Stability | Summary |
|---|---|---|---|---|---|
| GET | `/v1/admin/invite-codes` | auth | admin-console | stable | List Invite Codes |
| POST | `/v1/admin/invite-codes` | auth | admin-console | stable | Create Invite Code |
| GET | `/v1/admin/invite-codes/ui` | auth | admin-console | stable | Invite Codes Admin Ui |
| POST | `/v1/admin/invite-codes/{code}/disable` | auth | admin-console | stable | Disable Invite Code |

## infra_probe_api

| Method | Path | Owner | Clients | Stability | Summary |
|---|---|---|---|---|---|
| GET | `/v1/health/live` | platform | load-balancer, monitoring | stable | Live |
| GET | `/v1/health/metrics` | platform | load-balancer, monitoring | stable | Metrics |
| GET | `/v1/health/ready` | platform | load-balancer, monitoring | stable | Ready |

## deprecated_api

| Method | Path | Owner | Clients | Stability | Summary |
|---|---|---|---|---|---|
| GET | `/v1/plans` | plans | legacy-flutter | deprecated | List Plans Use /v1/schedule and /v1/care plan publications for the current product. |
| POST | `/v1/plans` | plans | legacy-flutter | deprecated | Create Plan Use /v1/schedule and /v1/care plan publications for the current product. |
| POST | `/v1/plans/tasks` | plans | legacy-flutter | deprecated | Create Task Use /v1/schedule and /v1/care plan publications for the current product. |
| GET | `/v1/plans/tasks/list` | plans | legacy-flutter | deprecated | List Tasks Use /v1/schedule and /v1/care plan publications for the current product. |
| DELETE | `/v1/plans/tasks/{task_id}` | plans | legacy-flutter | deprecated | Delete Task Use /v1/schedule and /v1/care plan publications for the current product. |
| PATCH | `/v1/plans/tasks/{task_id}` | plans | legacy-flutter | deprecated | Update Task Use /v1/schedule and /v1/care plan publications for the current product. |
| PATCH | `/v1/plans/tasks/{task_id}/completion` | plans | legacy-flutter | deprecated | Update Task Completion Use /v1/schedule and /v1/care plan publications for the current product. |
| PATCH | `/v1/plans/tasks/{task_id}/state` | plans | legacy-flutter | deprecated | Update Task State Use /v1/schedule and /v1/care plan publications for the current product. |
| DELETE | `/v1/plans/{plan_id}` | plans | legacy-flutter | deprecated | Delete Plan Use /v1/schedule and /v1/care plan publications for the current product. |
| GET | `/v1/plans/{plan_id}` | plans | legacy-flutter | deprecated | Get Plan Use /v1/schedule and /v1/care plan publications for the current product. |
| PATCH | `/v1/plans/{plan_id}/todos/{item_id}/completion` | plans | legacy-flutter | deprecated | Update Plan Todo Completion Use /v1/schedule and /v1/care plan publications for the current product. |
| GET | `/v1/pregnancy-diary/entries` | pregnancy-diary | legacy-flutter | deprecated | List Entries Use /v1/mother/diary for the current postpartum diary contract. |
| POST | `/v1/pregnancy-diary/entries` | pregnancy-diary | legacy-flutter | deprecated | Create Entry Use /v1/mother/diary for the current postpartum diary contract. |
| DELETE | `/v1/pregnancy-diary/entries/{entry_date}` | pregnancy-diary | legacy-flutter | deprecated | Delete Entry Use /v1/mother/diary for the current postpartum diary contract. |
| GET | `/v1/pregnancy-diary/entries/{entry_date}` | pregnancy-diary | legacy-flutter | deprecated | Get Entry Use /v1/mother/diary for the current postpartum diary contract. |
| PATCH | `/v1/pregnancy-diary/entries/{entry_date}` | pregnancy-diary | legacy-flutter | deprecated | Update Entry Use /v1/mother/diary for the current postpartum diary contract. |
