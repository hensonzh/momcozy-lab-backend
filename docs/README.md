# Product Backend Docs

This directory contains the Product Backend contracts, operational guides, and
product references. Agent orchestration, tools, prompts, context, memory,
workers, and evals belong to the independently deployed Agent Runtime repository.

## Product Assets

- `product-asset-storage.md`: product asset manifest and storage policy.

## API And Client Contracts

- `openapi.generated.json`: generated Product Backend OpenAPI snapshot.
- `api-surface-catalog.md`: generated Product Backend API surface
  classification.
- `api-contract-handoff.md`: human-readable public and internal API contract.
- `flutter-client-compatibility.md`: Product API client compatibility policy.
- `flutter-smoke-flows.json`: Product API mobile smoke scenarios.
- `vision-provider-integration.md`: owner-scoped schedule screenshot preview
  contract.

## Operations

- `deployment-runbook.md`: Product Backend deployment, rollback, and incident
  procedures.
- `release-smoke-checklist.md`: Product Backend release acceptance checklist.
- `environment-profiles.md`: local, test, and production environment rules.
- `postgres-integration-profile.md`: PostgreSQL integration checks.
- `object-storage-integration-profile.md`: object storage integration checks.

## Engineering Conventions

- `module-layering.md`: router, service, domain, repository, and internal Agent
  API adapter boundaries.

## Agent Runtime Boundary

The Product Backend owns user authentication, JWKS publication, product data,
owner scope, business actions, audit, and idempotency. It exposes only the
`/v1/internal/agent/*` service API to Agent Runtime. Mobile Agent conversation
routes and all runtime implementation documents are owned and published by the
Agent Runtime repository.
