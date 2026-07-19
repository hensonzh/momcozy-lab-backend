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
| GET | `/v1/agent/actions/{action_id}` | agent-runtime | flutter | stable | Get Action |
| POST | `/v1/agent/actions/{action_id}/confirm` | agent-runtime | flutter | stable | Confirm Action |
| POST | `/v1/agent/actions/{action_id}/reject` | agent-runtime | flutter | stable | Reject Action |
| DELETE | `/v1/agent/artifacts/{artifact_id}` | agent-runtime | flutter | stable | Delete Artifact |
| DELETE | `/v1/agent/facts` | agent-runtime | flutter | stable | Clear Facts |
| GET | `/v1/agent/facts` | agent-runtime | flutter | stable | List Facts |
| DELETE | `/v1/agent/facts/{fact_id}` | agent-runtime | flutter | stable | Delete Fact |
| GET | `/v1/agent/memories` | agent-runtime | flutter | stable | List Memories |
| GET | `/v1/agent/memories/settings` | agent-runtime | flutter | stable | Get Memory Settings |
| PUT | `/v1/agent/memories/settings` | agent-runtime | flutter | stable | Update Memory Settings |
| DELETE | `/v1/agent/memories/{memory_id}` | agent-runtime | flutter | stable | Delete Memory |
| POST | `/v1/agent/runs` | agent-runtime | flutter | stable | Create Run |
| GET | `/v1/agent/runs/{run_id}` | agent-runtime | flutter | stable | Get Run |
| POST | `/v1/agent/runs/{run_id}/cancel` | agent-runtime | flutter | stable | Cancel Run |
| POST | `/v1/agent/runs/{run_id}/client-events` | agent-runtime | flutter | stable | Record Client Event |
| GET | `/v1/agent/runs/{run_id}/events` | agent-runtime | flutter | stable | List Run Events |
| GET | `/v1/agent/threads` | agent-runtime | flutter | stable | List Threads |
| POST | `/v1/agent/threads` | agent-runtime | flutter | stable | Create Thread |
| GET | `/v1/agent/threads/{thread_id}` | agent-runtime | flutter | stable | Get Thread |
| GET | `/v1/assets` | product-assets | flutter | stable | List Product Assets |
| GET | `/v1/assets/{asset_id}` | product-assets | flutter | stable | Get Product Asset |
| POST | `/v1/auth/invite-login` | auth | flutter | stable | Invite Login |
| POST | `/v1/auth/login` | auth | flutter | stable | Login |
| POST | `/v1/auth/logout` | auth | flutter | stable | Logout |
| POST | `/v1/auth/refresh` | auth | flutter | stable | Refresh |
| POST | `/v1/auth/signup` | auth | flutter | stable | Signup |
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
| GET | `/v1/notifications` | notifications | flutter | stable | List Notifications |
| DELETE | `/v1/notifications/{notification_id}` | notifications | flutter | stable | Archive Notification |
| PATCH | `/v1/notifications/{notification_id}/read` | notifications | flutter | stable | Set Notification Read State |
| GET | `/v1/plans` | plans | flutter | stable | List Plans |
| POST | `/v1/plans` | plans | flutter | stable | Create Plan |
| POST | `/v1/plans/tasks` | plans | flutter | stable | Create Task |
| GET | `/v1/plans/tasks/list` | plans | flutter | stable | List Tasks |
| DELETE | `/v1/plans/tasks/{task_id}` | plans | flutter | stable | Delete Task |
| PATCH | `/v1/plans/tasks/{task_id}` | plans | flutter | stable | Update Task |
| PATCH | `/v1/plans/tasks/{task_id}/completion` | plans | flutter | stable | Update Task Completion |
| PATCH | `/v1/plans/tasks/{task_id}/state` | plans | flutter | stable | Update Task State |
| DELETE | `/v1/plans/{plan_id}` | plans | flutter | stable | Delete Plan |
| GET | `/v1/plans/{plan_id}` | plans | flutter | stable | Get Plan |
| PATCH | `/v1/plans/{plan_id}/todos/{item_id}/completion` | plans | flutter | stable | Update Plan Todo Completion |
| GET | `/v1/pregnancy-diary/entries` | pregnancy-diary | flutter | stable | List Entries |
| POST | `/v1/pregnancy-diary/entries` | pregnancy-diary | flutter | stable | Create Entry |
| DELETE | `/v1/pregnancy-diary/entries/{entry_date}` | pregnancy-diary | flutter | stable | Delete Entry |
| GET | `/v1/pregnancy-diary/entries/{entry_date}` | pregnancy-diary | flutter | stable | Get Entry |
| PATCH | `/v1/pregnancy-diary/entries/{entry_date}` | pregnancy-diary | flutter | stable | Update Entry |
| GET | `/v1/profile/infants` | profiles | flutter | stable | List My Infants |
| POST | `/v1/profile/infants` | profiles | flutter | stable | Create My Infant |
| GET | `/v1/profile/me` | profiles | flutter | stable | Get My Profile |
| PUT | `/v1/profile/me` | profiles | flutter | stable | Update My Profile |
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
| POST | `/v1/speech/transcribe-chunk` | voice | flutter | stable | Transcribe Speech Chunk |
| GET | `/v1/support/tickets` | support | flutter | stable | List Support Tickets |
| POST | `/v1/support/tickets` | support | flutter | stable | Create Support Ticket |
| GET | `/v1/support/tickets/{ticket_id}` | support | flutter | stable | Get Support Ticket |

## runtime_stream_api

| Method | Path | Owner | Clients | Stability | Summary |
|---|---|---|---|---|---|
| GET | `/v1/agent/runs/{run_id}/stream` | agent-runtime | flutter | stable | Stream Run Events |
| GET | `/v1/files/{file_id}/vision/events/stream` | files | flutter | stable | Stream File Vision Events |
| GET | `/v1/realtime-voice-stream` | voice | flutter | stable | Realtime Voice Stream |

## internal_service_api

| Method | Path | Owner | Clients | Stability | Summary |
|---|---|---|---|---|---|
| POST | `/v1/notifications` | notifications | agent-worker, outbox-worker | stable | Create Notification |

## admin_ops_api

| Method | Path | Owner | Clients | Stability | Summary |
|---|---|---|---|---|---|
| GET | `/v1/admin/invite-codes` | auth | admin-console | stable | List Invite Codes |
| POST | `/v1/admin/invite-codes` | auth | admin-console | stable | Create Invite Code |
| GET | `/v1/admin/invite-codes/ui` | auth | admin-console | stable | Invite Codes Admin Ui |
| POST | `/v1/admin/invite-codes/{code}/disable` | auth | admin-console | stable | Disable Invite Code |
| POST | `/v1/agent/admin/runs/{run_id}/eval-cases` | agent-runtime | ops-console, eval-runner | stable | Create Eval Case From Run |
| GET | `/v1/agent/admin/runs/{run_id}/replay` | agent-runtime | ops-console, eval-runner | stable | Export Run Replay Bundle |

## infra_probe_api

| Method | Path | Owner | Clients | Stability | Summary |
|---|---|---|---|---|---|
| GET | `/v1/health/live` | platform | load-balancer, monitoring | stable | Live |
| GET | `/v1/health/metrics` | platform | load-balancer, monitoring | stable | Metrics |
| GET | `/v1/health/ready` | platform | load-balancer, monitoring | stable | Ready |
