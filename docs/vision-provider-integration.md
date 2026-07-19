# Vision Provider Integration

## Contract

`GET /v1/files/{file_id}/vision/events/stream?purpose=schedule` is the only
production Schedule screenshot-recognition entry point. It uses the normal
`Authorization: Bearer <access_token>` header, resolves `{file_id}` with the
authenticated user's owner scope, and returns server-sent events. Tokens are
never accepted in the URL.

The flow is read-only. It reads the existing object and returns an editable
preview; it does not write plans, tasks, feeding records, pumping records,
artifacts, or provider output. The client must show the preview and use the
normal Schedule mutation endpoints only after the user chooses to save it.

Event order:

1. `vision.started`
2. zero to 32 `vision.schedule_task.preview` events
3. `vision.completed`

Each task preview payload is:

```json
{
  "provider": "openai",
  "purpose": "schedule",
  "time": "09:30",
  "event": "吸奶",
  "event_type": "pump"
}
```

`time` is strict 24-hour `HH:mm`; `event` is the editable title with 1-80
characters; `event_type` is one of `pump`, `breastfeed`, or `custom`. Unclear,
unrelated, or task-free screenshots complete with `event_count=0` rather than
inventing tasks.

## Providers

- `disabled`: default, stable `503` / `vision_provider_disabled`.
- `local_stub`: deterministic two-task preview for local and automated contract
  tests; startup rejects it in production.
- `openai`: uses the OpenAI Responses API image input and Pydantic Structured
  Outputs. It reuses `OPENAI_API_KEY`, uses `VISION_OPENAI_MODEL`, disables SDK
  retries, sets `store=false`, and applies both SDK and coroutine timeouts from
  `VISION_REQUEST_TIMEOUT_SECONDS`.

The OpenAI adapter sends an in-memory base64 data URL. It does not send the
MomCozy file ID, object key, original filename, app access token, or user ID.
Supported inputs are PNG, JPEG, and WEBP. The application upload limit remains
the outer byte bound.

Official API references:

- <https://developers.openai.com/api/docs/guides/images-vision>
- <https://developers.openai.com/api/docs/guides/structured-outputs>

## Configuration

```env
VISION_PROVIDER=openai
OPENAI_API_KEY=secret-from-deployment-manager
VISION_OPENAI_MODEL=gpt-5.4-mini
VISION_REQUEST_TIMEOUT_SECONDS=20
```

Never commit the key. Production startup fails closed when `openai` is selected
without a key, model, or positive timeout.

## Stable Errors

| HTTP | Code | Meaning |
|---:|---|---|
| 422 | `validation_failed` | Uploaded object is not a supported image. |
| 429 | `vision_provider_rate_limited` | Provider quota or rate limit reached. |
| 502 | `vision_provider_invalid_response` | Refusal, incomplete, or invalid structured output. |
| 502 | `vision_provider_rejected_request` | Provider rejected an otherwise valid adapter request. |
| 502 | `vision_provider_failed` | Unclassified provider failure. |
| 503 | `vision_provider_disabled` | Provider remains disabled. |
| 503 | `vision_provider_not_configured` | SDK, key, model, or structured client is unavailable. |
| 503 | `vision_provider_auth_failed` | Provider rejected deployment credentials. |
| 503 | `vision_provider_unavailable` | Provider-side 5xx. |
| 504 | `vision_provider_timeout` | Hard request deadline elapsed. |

Provider exception text and credentials are not returned in API details.

## Release Gate

Unit/API contract tests are deterministic and do not call the external model.
Before changing production from `disabled` to `openai`, run an environment-owned
smoke set with at least:

- clear pump, direct-breastfeeding, and custom tasks;
- mixed 12-hour and 24-hour time labels;
- duplicate times and more than ten rows;
- rotated, low-contrast, and small-text screenshots;
- unrelated images and screenshots with no readable time;
- cross-owner file access and an expired bearer token;
- provider timeout, invalid credentials, and rate-limit simulation.
- a deployment-owned vision concurrency/rate budget sized for 10 MB high-detail
  inputs, with provider quota alerts and overload rejection verified.

Acceptance is zero fabricated tasks on the negative set, schema-valid output on
every successful request, no owner-scope bypass, no API/provider output
persistence, and human review of time/title accuracy on the positive set.
