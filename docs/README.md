# Production Backend Docs

This directory contains current production contracts, operational guides, and
product references. Completed migration plans, refactor inventories, and
legacy-route comparison documents do not belong here.

## Agent And Product

- `main-agent-design.md`: discussion draft for the simplified main-agent,
  bounded capability tools, specialist handoffs, and response ownership.
- `agent-tool-catalog.md`: model-visible tools, static allowlists, action
  boundaries, and internal non-model handlers.
- `product/momcozy-mai-integrated-prd.md`: current product requirements and
  user journeys.
- `product/momcozy-agent-service-test-plan.md`: product-level agent scenarios
  and acceptance criteria.

## API And Client Contracts

- `openapi.generated.json`: generated OpenAPI contract snapshot.
- `api-surface-catalog.md`: generated API surface classification.
- `api-contract-handoff.md`: human-readable API and agent event contract.
- `flutter-client-compatibility.md`: generated-client compatibility policy.
- `flutter-smoke-flows.json`: mobile integration smoke scenarios.
- `vision-provider-integration.md`: owner-scoped schedule screenshot preview,
  OpenAI adapter, error contract, and rollout quality gate.

## Operations

- `deployment-runbook.md`: deployment, rollback, recovery, and incident steps.
- `release-smoke-checklist.md`: release acceptance checklist.
- `environment-profiles.md`: local, test, and production environment rules.
- `postgres-integration-profile.md`: PostgreSQL integration checks.
- `redis-runtime-profile.md`: Redis runtime checks.
- `object-storage-integration-profile.md`: object storage integration checks.
- `product-asset-storage.md`: product asset manifest and storage policy.

## Engineering Conventions

- `module-layering.md`: router, service, domain, and repository boundaries.
