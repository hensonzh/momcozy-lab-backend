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
| POST | `/v1/auth/change-password` | auth | flutter | stable | Change Password |
| POST | `/v1/auth/forgot-password` | auth | flutter | stable | Forgot Password |
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
| POST | `/v1/auth/verify-registration-code` | auth | flutter | stable | Verify Registration Code |
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
| GET | `/v1/lactation/records` | lactation | flutter | stable | List Records |
| POST | `/v1/lactation/records` | lactation | flutter | stable | Create Record |
| DELETE | `/v1/lactation/records/{record_id}` | lactation | flutter | stable | Delete Record |
| PUT | `/v1/lactation/records/{record_id}` | lactation | flutter | stable | Update Record |
| POST | `/v1/lactation/records/{record_id}/restore` | lactation | flutter | stable | Restore Record |
| GET | `/v1/model-assets/{token}` | files | openai-responses | stable | Get Agent Model Asset Opaque bearer capability used only for model input fetches. |
| GET | `/v1/notifications` | notifications | flutter | stable | List Notifications |
| POST | `/v1/notifications/installations` | notifications | flutter | stable | Register Push Installation |
| POST | `/v1/notifications/installations/{installation_id}/detach` | notifications | flutter | stable | Detach Push Installation |
| GET | `/v1/notifications/preferences` | notifications | flutter | stable | Notification Preferences |
| PATCH | `/v1/notifications/preferences/{category}` | notifications | flutter | stable | Update Notification Preference |
| POST | `/v1/notifications/read-all` | notifications | flutter | stable | Mark All Notifications Read |
| DELETE | `/v1/notifications/{notification_id}` | notifications | flutter | stable | Archive Notification |
| POST | `/v1/notifications/{notification_id}/open` | notifications | flutter | stable | Open Notification |
| PATCH | `/v1/notifications/{notification_id}/read` | notifications | flutter | stable | Set Notification Read State |
| GET | `/v1/onboarding/me` | onboarding | flutter | stable | Read Onboarding |
| PUT | `/v1/onboarding/me/profile` | onboarding | flutter | stable | Confirm Onboarding |
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
| POST | `/v1/internal/agent/files/resolve` | files | agent-runtime | stable | Resolve Agent File |
| GET | `/v1/internal/agent/files/{file_id}/model-asset` | files | agent-runtime | stable | Get Model Asset Bytes |
| GET | `/v1/internal/agent/files/{file_id}/model-image` | files | agent-runtime | stable | Get Local Model Image |
| POST | `/v1/internal/agent/notifications/reply-ready` | notifications | agent-runtime | stable | Agent Reply Ready |
| GET | `/v1/internal/agent/profile` | profiles | agent-runtime | stable | Read Agent Profile |
| GET | `/v1/internal/agent/records` | profiles | agent-runtime | stable | Read Agent Topical Records |
| POST | `/v1/internal/agent/records/batch` | profiles | agent-runtime | stable | Write Agent Records |
| GET | `/v1/internal/agent/schedule` | profiles | agent-runtime | stable | Read Agent Schedule |
| POST | `/v1/internal/agent/schedule/batch` | profiles | agent-runtime | stable | Write Agent Schedule |

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
| GET | `/v1/plans` | plans | legacy-flutter | deprecated | List Plans Use /v1/schedule for personal schedule entries. |
| POST | `/v1/plans` | plans | legacy-flutter | deprecated | Create Plan Use /v1/schedule for personal schedule entries. |
| POST | `/v1/plans/tasks` | plans | legacy-flutter | deprecated | Create Task Use /v1/schedule for personal schedule entries. |
| GET | `/v1/plans/tasks/list` | plans | legacy-flutter | deprecated | List Tasks Use /v1/schedule for personal schedule entries. |
| DELETE | `/v1/plans/tasks/{task_id}` | plans | legacy-flutter | deprecated | Delete Task Use /v1/schedule for personal schedule entries. |
| PATCH | `/v1/plans/tasks/{task_id}` | plans | legacy-flutter | deprecated | Update Task Use /v1/schedule for personal schedule entries. |
| PATCH | `/v1/plans/tasks/{task_id}/completion` | plans | legacy-flutter | deprecated | Update Task Completion Use /v1/schedule for personal schedule entries. |
| PATCH | `/v1/plans/tasks/{task_id}/state` | plans | legacy-flutter | deprecated | Update Task State Use /v1/schedule for personal schedule entries. |
| DELETE | `/v1/plans/{plan_id}` | plans | legacy-flutter | deprecated | Delete Plan Use /v1/schedule for personal schedule entries. |
| GET | `/v1/plans/{plan_id}` | plans | legacy-flutter | deprecated | Get Plan Use /v1/schedule for personal schedule entries. |
