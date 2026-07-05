# Production Backend Refactor Docs

Use this directory for documents that guide or verify the new production backend:

- API inventory.
- SQLite schema inventory.
- Tool and action inventory.
- Event contract inventory.
- Migration decisions and ADRs.
- Operational runbooks.
- Engineering-loop acceptance gates.

The first document to add should be `backend-refactor-inventory.md`.

Key documents:

- `backend-refactor-inventory.generated.md`: generated legacy route/table risk
  inventory.
- `productization-roadmap.md`: Phase 0-6 backend-only productization roadmap,
  status matrix, Flutter boundary, and remaining PR slices.
- `agent-runtime-continuation-migration-plan.md`: continuation plan for taking
  the agent runtime foundation to complete product-level agent behavior.
- `legacy-backend-acceptance-loop.md`: maps legacy domains to production
  acceptance tests, evals, and Codex loop gates.
- `environment-profiles.md`: local, staging, and production environment
  switching rules.
- `product-asset-storage.md`: manifest + object storage policy for large
  product assets.
- `module-layering.md`: router/service/domain/repository boundary convention
  for production modules.
