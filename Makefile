BACKEND_ENV ?= local
BACKEND_ENV_FILE ?= production_backend/env/$(BACKEND_ENV).env.example
COMPOSE_ENV_FILE ?= production_backend/env/compose.local.env.example
PYTHON ?= production_backend/.venv/bin/python
COMPOSE_ENV_FILE_FOR_COMPOSE = $(patsubst production_backend/%,%,$(COMPOSE_ENV_FILE))
COMPOSE = MOMCOZY_BACKEND_ENV_FILE=$(COMPOSE_ENV_FILE_FOR_COMPOSE) docker compose -f production_backend/docker-compose.yml

.PHONY: backend-local-up backend-up backend-down backend-local-migrate backend-migrate backend-local-workers backend-workers backend-local-minio backend-export-contracts backend-check-infra backend-productization-status backend-smoke backend-staging-smoke backend-production-readiness backend-worker-backlog backend-agent-recover-stuck-runs backend-env-print

backend-local-up:
	$(MAKE) backend-up BACKEND_ENV=local

backend-up:
	$(COMPOSE) up -d postgres redis minio minio-init api

backend-down:
	$(COMPOSE) down

backend-local-migrate:
	$(MAKE) backend-migrate BACKEND_ENV=local

backend-migrate:
	$(COMPOSE) --profile tools run --rm migrate

backend-local-workers:
	$(MAKE) backend-workers BACKEND_ENV=local

backend-workers:
	$(COMPOSE) --profile workers up -d agent-worker outbox-worker

backend-local-minio:
	$(COMPOSE) up -d minio minio-init

backend-export-contracts:
	$(PYTHON) production_backend/scripts/export_openapi.py --output production_backend/docs/openapi.generated.json
	$(PYTHON) production_backend/scripts/export_api_surface_catalog.py --openapi-input production_backend/docs/openapi.generated.json --output production_backend/docs/api-surface-catalog.md

backend-check-infra:
	set -a; . $(BACKEND_ENV_FILE); set +a; \
	$(PYTHON) production_backend/scripts/check_database_profile.py; \
	$(PYTHON) production_backend/scripts/check_redis_runtime_controls.py; \
	$(PYTHON) production_backend/scripts/check_object_storage_profile.py; \
	$(PYTHON) production_backend/scripts/check_product_asset_storage.py

backend-productization-status:
	$(PYTHON) production_backend/scripts/check_productization_status.py

backend-smoke:
	$(PYTHON) production_backend/scripts/check_productization_status.py
	$(PYTHON) production_backend/scripts/run_agent_seed_eval.py

backend-staging-smoke:
	$(MAKE) backend-productization-status
	$(MAKE) backend-check-infra BACKEND_ENV=staging

backend-production-readiness:
	$(MAKE) backend-productization-status
	$(MAKE) backend-check-infra BACKEND_ENV=production

backend-worker-backlog:
	set -a; . $(BACKEND_ENV_FILE); set +a; \
	$(PYTHON) production_backend/scripts/inspect_worker_backlog.py

backend-agent-recover-stuck-runs:
	set -a; . $(BACKEND_ENV_FILE); set +a; \
	$(PYTHON) production_backend/scripts/recover_stuck_agent_runs.py

backend-env-print:
	@echo "BACKEND_ENV=$(BACKEND_ENV)"
	@echo "BACKEND_ENV_FILE=$(BACKEND_ENV_FILE)"
	@echo "COMPOSE_ENV_FILE=$(COMPOSE_ENV_FILE)"
	@echo "PYTHON=$(PYTHON)"
