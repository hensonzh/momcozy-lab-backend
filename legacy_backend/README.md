# Legacy Backend

This directory contains the old MomCozyAgent demo backend, moved out of the
repository root so the production backend can evolve in isolation.

Contents:

- `src/`: old `momcozy_agent` package, including the handwritten agent loop,
  static API-key auth, in-memory sessions, SQLite data store, and tool handlers.
- `skills/`: old service skill prompt files and references.
- `tests/`: old backend tests for the demo implementation.
- `scripts/`: old local/demo scripts.
- `web_data/`: old local admin UI.
- `data/`, `upload_files/`, `runlogs/`, `logs/`: legacy local data, uploads,
  and runtime artifacts.

Production code must not import modules from this directory. Product assets
still used by the new backend have been promoted to
`production_backend/fixtures/product_assets/`.
