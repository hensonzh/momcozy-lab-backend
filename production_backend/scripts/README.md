# Scripts

Use this directory for explicit migration, verification, and operational scripts.

Scripts should use production backend settings and service/repository boundaries
where possible.

- `run_agent_seed_eval.py`: validates product agent seed cases with deterministic
  assertions. By default it performs a seed self-check; pass `--trace-fixtures`
  to evaluate observed traces from a runtime/eval client. Use `--output` for
  JSON summary and `--junit-output` for CI test reports.
- `run_agent_replay_eval.py`: evaluates a redacted replay bundle against one
  product seed eval case.
- `check_productization_status.py`: checks backend-local productization
  guardrails such as required docs, env profiles, CI gates, worker scripts,
  OpenAPI snapshot, Makefile smoke targets, and legacy-runtime scan coverage.
- `inspect_worker_backlog.py`: prints durable outbox and agent-run backlog
  counts without mutating state.
- `recover_stuck_agent_runs.py`: dry-runs stuck `running` agent run recovery by
  default; pass `--apply` to mark matched runs failed and clear Redis controls.
