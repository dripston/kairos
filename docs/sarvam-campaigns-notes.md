# Sarvam campaigns, retries, workflows and webhooks — notes for K27 (read-only)

Read on 19 Nov 2026 from CreditNirvana's live Sarvam workspace through the `sarvam-voice-agents` MCP,
**list/get operations only**. Nothing was created, started, paused, edited or deleted. No cohort file,
DND entry or call record was opened (they hold phone numbers). Hostnames, tokens and caller IDs seen in
configs are deliberately left out of this file.

**Source caveat.** The `sarvam://playbooks/*` and `sarvam://schemas/webhook-payload` resources could not
be read in this session (the MCP resource reader was not available to the agent). Everything below comes
from the tools' own published schemas (`configure_campaign`, `upload_cohort`, `campaigns`, `workflows`,
`dnd`, `experiments`) and from reading real campaign and workflow configs. Re-check against
`sarvam://schemas/webhook-payload` before going LIVE.

## Campaign = one agent version (or experiment) + a dial list + a schedule

`configure_campaign` (create or patch; `dry_run=true` checks the body without creating anything):

| Field | Meaning |
|---|---|
| `name`, `start_timestamp`, `end_timestamp` | required on create; start and description are fixed afterwards |
| `allowed_schedule` | `{timezone: Asia/Kolkata, allowed_start_time "HH:MM", allowed_end_time, allowed_days [Title Case]}`; required on create |
| `app_config.app_type` | `agent` (pinned `app_id` + committed `app_version`, or `experiment_id`) or `workflow` (`app_id` + `app_version` only, cannot be patched) |
| `app_config.attempts_per_second` | dial rate 0.1–500 |
| `app_config.connection_configs` | `[{connection_id, phone_numbers[], weight}]`, chosen via `telephony list_connections` → `get_connection include=[endpoints]` |
| `app_config.retry_config` | see below |
| `app_config.webhook_config` | `{url, metadata{...}}` — a call-end webhook for every attempt; metadata is echoed |

Live CN campaigns seen (agent type): dial 1/s, schedule 09:00–21:00 or 09:30–18:45 IST (some all 7 days),
`webhook_config.metadata = {batch_id, outbound_campaign_id}` pointing at CN's AICC call-event endpoint.

## Cohort upload (the dial list)

`upload_cohort(parent="campaign", parent_id, name, content=<CSV text>, transformation)`:

```json
{"phone_number": {"column_name": "phone", "country_code": "IN"},
 "user_identifier": {"column_name": "customer_id"},
 "app_variables": {"user_name": {"column_name": "name", "required": true}},
 "alternate_phone_numbers": [{"column_name": "alt_phone"}],
 "app_overrides": {"initial_language_name": {"column_name": "lang"}}}
```

- Every `app_variables` key must exist in the **committed** agent config, otherwise it loads, reports the
  row accepted, and reaches the call as nothing.
- Formatting functions per column: `indian_currency`, `date`, `string_replace`, `string_strip`,
  `string_capitalize`, `indian_state_language_map`, `format_language_name`.
- A bad transformation rejects the whole upload; a bad row is rejected alone and counted. Poll
  `campaigns get_cohort` until terminal; `file_type=rejected_records` says why rows failed.
- `exclude_cohort` removes people already on a list; `stream_cohort_users` streams rows in.
- CN uploads several batches per campaign (e.g. 4 cohorts of ~1–5k rows, `source_type: file_upload`).

## Retry / redial

`retry_config`:

```json
{"max_retries": 2, "retry_interval_minutes": [5, 5],
 "retry_on": {"busy": {"enabled": true}, "no_answer": {"enabled": true}, "failed": {"enabled": false},
              "short_duration": {"enabled": true, "threshold_seconds": 10},
              "provider_error": {"enabled": true, "max_retries": 2, "retry_interval_minutes": 5},
              "internal_error": {"enabled": true, "max_retries": 2, "retry_interval_minutes": 5}}}
```

- `retry_interval_minutes` is a list with exactly `max_retries` entries, each ≥ 5.
- `max_retries > 0` needs at least one of busy / failed / no_answer / short_duration enabled (provider and
  internal errors do not count; they carry their own counts and a scalar interval).
- Outcome-based: busy, no answer, failed, short duration (connected but under N seconds).
- **What CN actually does:** the live agent campaigns have Sarvam-native retries **off**
  (`max_retries: 0`, all `retry_on` disabled). Redial is owned by CN's own server ("AICC").

## CN's "auto churn" = a committed Sarvam workflow driven by the AICC server

The workspace holds ~23 workflows named `cn-ac-*` (description "Auto churn, published by AICC").
The committed one read (`cn-ac-pre-ugro-capital-…`, version 1) is a loop:

```
start -> gate (HTTP POST to AICC, event "before_call")
      -> switch: ac_next == "dial"            -> telephony node (agent, caller line, agent_variables)
                 ac_until matches ISO-8601 UTC -> wait until ac_until -> gate
                 otherwise                     -> end
telephony -> report (HTTP POST to AICC, event "call_ended")
      -> switch: ac_until set -> wait -> gate ; otherwise -> end
```

- The gate / report body (protocol `v: 2`) carries `line, step, event, status, duration, workflow,
  attempt_id, end_datetime, recording_url, call_result_id, failure_reason, interaction_id,
  final_agent_variables` (the last as a JSON string).
- The server answers `{ac_next, ac_until, ac_step, ac_line}`: dial now, wait until a time, or end, and
  which caller line (`cn_line`) to rotate to. So CN's churn of dialling lists = **server-decided redials
  with waits**, one workflow run per borrower, not Sarvam's retry_config.
- Workflow HTTP nodes retry 4× at 15 s; the telephony node exposes `status | connectivity_status`,
  `attempt_id`, `interaction_id`, `duration`, `end_datetime`, `failure_reason`, `recording_url`,
  `final_agent_variables`.
- Reports: `workflows report workflow_campaigns | workflow_overview | workflow_node_metrics |
  workflow_agent_metrics | workflow_http_metrics | workflow_runs | workflow_run`.

**K27 mapping:** `churn/sarvam_export.gate_response()` speaks this gate protocol. Kairos answers
`dial` only when the borrower's lifecycle is ACTIVE and today's journey step allows voice in the current
layer; it answers "wait until next contact morning" while paused and "end" for STOPPED / PAID / EXHAUSTED
/ HARDSHIP. That keeps every redial inside K01 caps, K13 holds and the K27 stop states.

## Webhooks

- `webhook_config.url` receives a POST after **every call attempt, connected or not**; `metadata` is echoed.
- `campaigns list_webhooks` lists deliveries (attempt_id, status, start/close time) and `retry_webhooks`
  re-delivers failures (not used here).
- Fields Kairos relies on (from the live workflow protocol): `attempt_id` (idempotency key),
  `interaction_id`, `status`/`connectivity_status`, `duration` (string), `end_datetime` (UTC `Z`),
  `failure_reason`, `final_agent_variables` (JSON string with the bot's post-call variables:
  `disposition`, `disposition_confidence`, `ptp_date`, `callback_at`, `touch_id`, ...).

## DND

Workspace-wide list; campaigns skip listed numbers automatically. Operations: list, lookup, add, remove
(remove needs `confirmed=true`). Bulk import is a client-side file upload. Kairos's STOP / opt-out is the
source of truth for consent (K01 STOP_OPT_OUT, all channels); syncing a STOP to Sarvam DND would be an
`add` and needs an explicit go-ahead (it changes CN's live workspace).

## Experiments

A traffic split across committed versions of one agent; a campaign runs one by naming `experiment_id`
in `app_config`. No update or delete. `variant_metrics` gives per-variant metrics with a best-variant
pointer. K27 does not use experiments; the K09 learner and K27 layers decide channel and slot.

## How K27 uses this (dry run only)

- `cohort_upload(engine, day, reserved_touches)` builds an `upload_cohort` body for the voicebot step members
  that have a **reserved ledger touch**: `reserved_touches` maps borrower to the instruction id the Supervisor
  reserved for that day's voice step (or the Supervisor decision). `user_identifier` = the `touch_id` agent
  variable = that `instruction_id` (`INS-<16 hex>`), which is what the voice engine's webhook join looks for
  (`initial_agent_variables.touch_id`, then `user_identifier`, then `metadata.touch_id`: the first that is a
  ledger touch). Without it the call arrives UNMATCHED. A borrower with no reservation (K06 gate refused, WAIT,
  CANCEL) is left out and listed under `skipped` with the gate's reason: never dial without a reservation.
  First name only, no amounts or loan numbers. The phone column is a `PHONE_REF:<borrower>` token; the real
  number is resolved at dial time outside this repo, inside the lender system.
- `campaign_config()` builds a `configure_campaign` body with `dry_run: true`, a schedule inside the K01
  window (Sundays off), the K17 agent for the use case, and a **K01-safe retry plan**: every redial is a
  voice contact, so `1 + max_retries ≤ per-channel daily cap − attempts already made today`, intervals
  ≥ the K01 same-channel gap (120 min).
- `webhook_to_events()` maps one attempt to ONE normalized `call` event (`sarvam:<attempt_id>`), with the
  disposition and confidence on it; K27 applies the K17 confidence thresholds. `before_call` gate events
  are not outcomes and are ignored.
