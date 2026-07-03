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
- `legacy-backend-acceptance-loop.md`: maps legacy domains to production
  acceptance tests, evals, and Codex loop gates.
