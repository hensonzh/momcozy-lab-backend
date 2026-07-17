COMPOSE_ENV_FILE ?= production_backend/env/compose.local.env
TEST_COMPOSE_ENV_FILE ?= production_backend/env/compose.test.env
PROD_COMPOSE_ENV_FILE ?= production_backend/env/compose.prod.env
BACKEND_ENV_FILE ?= $(COMPOSE_ENV_FILE)
PYTHON ?= production_backend/.venv/bin/python
BACKEND_BUILD_FLAGS ?=
COMPOSE_ENV_FILE_FOR_COMPOSE = $(patsubst production_backend/%,%,$(COMPOSE_ENV_FILE))
COMPOSE = MOMCOZY_BACKEND_ENV_FILE=$(COMPOSE_ENV_FILE_FOR_COMPOSE) docker compose -f production_backend/docker-compose.local.yml
TEST_COMPOSE_ENV_FILE_FOR_COMPOSE = $(patsubst production_backend/%,%,$(TEST_COMPOSE_ENV_FILE))
TEST_COMPOSE = MOMCOZY_BACKEND_ENV_FILE=$(TEST_COMPOSE_ENV_FILE_FOR_COMPOSE) docker compose -f production_backend/docker-compose.test.yml
PROD_COMPOSE_ENV_FILE_FOR_COMPOSE = $(patsubst production_backend/%,%,$(PROD_COMPOSE_ENV_FILE))
PROD_COMPOSE = MOMCOZY_BACKEND_ENV_FILE=$(PROD_COMPOSE_ENV_FILE_FOR_COMPOSE) docker compose -f production_backend/docker-compose.prod.yml

.PHONY: backend-local-build backend-build backend-local-up backend-up backend-down backend-local-migrate backend-migrate backend-local-workers backend-workers backend-local-minio backend-test-build backend-test-migrate backend-test-up backend-test-services backend-test-down backend-test-ps backend-test-logs backend-prod-build backend-prod-migrate backend-prod-up backend-prod-services backend-prod-down backend-prod-ps backend-prod-logs backend-export-contracts backend-check-infra backend-productization-status backend-smoke backend-agent-device-decision-eval backend-test-smoke backend-prod-readiness backend-worker-backlog backend-agent-recover-stuck-runs backend-env-print

backend-local-build:
	$(MAKE) backend-build

backend-build:
	$(COMPOSE) --profile tools --profile workers build $(BACKEND_BUILD_FLAGS) migrate api agent-worker outbox-worker memory-worker

backend-local-up:
	$(MAKE) backend-up

backend-up:
	$(MAKE) backend-build COMPOSE_ENV_FILE=$(COMPOSE_ENV_FILE) BACKEND_BUILD_FLAGS="$(BACKEND_BUILD_FLAGS)"
	$(COMPOSE) up -d postgres redis minio minio-init
	$(COMPOSE) --profile tools run --rm migrate
	$(COMPOSE) --profile workers up -d --force-recreate api agent-worker outbox-worker memory-worker

backend-down:
	$(COMPOSE) down

backend-local-migrate:
	$(MAKE) backend-migrate

backend-migrate:
	$(COMPOSE) --profile tools build $(BACKEND_BUILD_FLAGS) migrate
	$(COMPOSE) --profile tools run --rm migrate

backend-local-workers:
	$(MAKE) backend-workers

backend-workers:
	$(COMPOSE) --profile workers build $(BACKEND_BUILD_FLAGS) agent-worker outbox-worker memory-worker
	$(COMPOSE) --profile workers up -d --force-recreate agent-worker outbox-worker memory-worker

backend-local-minio:
	$(COMPOSE) up -d minio minio-init

backend-test-build:
	$(TEST_COMPOSE) --profile tools --profile workers build $(BACKEND_BUILD_FLAGS) migrate api agent-worker outbox-worker memory-worker

backend-test-migrate:
	$(TEST_COMPOSE) --profile tools build $(BACKEND_BUILD_FLAGS) migrate
	$(TEST_COMPOSE) --profile tools run --rm migrate

backend-test-up:
	$(MAKE) backend-test-build TEST_COMPOSE_ENV_FILE=$(TEST_COMPOSE_ENV_FILE) BACKEND_BUILD_FLAGS="$(BACKEND_BUILD_FLAGS)"
	$(TEST_COMPOSE) up -d postgres redis minio minio-init
	$(MAKE) backend-test-migrate TEST_COMPOSE_ENV_FILE=$(TEST_COMPOSE_ENV_FILE)
	$(TEST_COMPOSE) up -d --force-recreate api agent-worker outbox-worker memory-worker

backend-test-services:
	$(TEST_COMPOSE) --profile workers build $(BACKEND_BUILD_FLAGS) api agent-worker outbox-worker memory-worker
	$(TEST_COMPOSE) up -d --force-recreate api agent-worker outbox-worker memory-worker

backend-test-down:
	$(TEST_COMPOSE) down

backend-test-ps:
	$(TEST_COMPOSE) ps

backend-test-logs:
	$(TEST_COMPOSE) logs -f api agent-worker outbox-worker memory-worker

backend-prod-build:
	$(PROD_COMPOSE) --profile tools --profile workers build $(BACKEND_BUILD_FLAGS) migrate api agent-worker outbox-worker memory-worker

backend-prod-migrate:
	$(PROD_COMPOSE) --profile tools build $(BACKEND_BUILD_FLAGS) migrate
	$(PROD_COMPOSE) --profile tools run --rm migrate

backend-prod-up:
	$(MAKE) backend-prod-build PROD_COMPOSE_ENV_FILE=$(PROD_COMPOSE_ENV_FILE) BACKEND_BUILD_FLAGS="$(BACKEND_BUILD_FLAGS)"
	$(MAKE) backend-prod-migrate PROD_COMPOSE_ENV_FILE=$(PROD_COMPOSE_ENV_FILE)
	$(PROD_COMPOSE) up -d --force-recreate api agent-worker outbox-worker memory-worker

backend-prod-services:
	$(PROD_COMPOSE) --profile workers build $(BACKEND_BUILD_FLAGS) api agent-worker outbox-worker memory-worker
	$(PROD_COMPOSE) up -d --force-recreate api agent-worker outbox-worker memory-worker

backend-prod-down:
	$(PROD_COMPOSE) down

backend-prod-ps:
	$(PROD_COMPOSE) ps

backend-prod-logs:
	$(PROD_COMPOSE) logs -f api agent-worker outbox-worker memory-worker

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
	$(PYTHON) -m pytest -q production_backend/tests/test_agent_task8_observed_eval.py
	$(PYTHON) production_backend/scripts/run_agent_fact_eval.py

backend-agent-device-decision-eval:
	set -a; . $(BACKEND_ENV_FILE); set +a; \
	$(PYTHON) production_backend/scripts/run_device_unboxing_decision_eval.py \
		--output /tmp/device-unboxing-decision-eval.json \
		--trace-output /tmp/device-unboxing-decision-traces.json
	@echo "report: /tmp/device-unboxing-decision-eval.json"
	@echo "provider traces: /tmp/device-unboxing-decision-traces.json"

backend-test-smoke:
	$(MAKE) backend-productization-status
	$(MAKE) backend-check-infra BACKEND_ENV_FILE=$(TEST_COMPOSE_ENV_FILE)

backend-prod-readiness:
	$(MAKE) backend-productization-status
	$(MAKE) backend-check-infra BACKEND_ENV_FILE=$(PROD_COMPOSE_ENV_FILE)

backend-worker-backlog:
	set -a; . $(BACKEND_ENV_FILE); set +a; \
	$(PYTHON) production_backend/scripts/inspect_worker_backlog.py

backend-agent-recover-stuck-runs:
	set -a; . $(BACKEND_ENV_FILE); set +a; \
	$(PYTHON) production_backend/scripts/recover_stuck_agent_runs.py

backend-env-print:
	@echo "BACKEND_ENV_FILE=$(BACKEND_ENV_FILE)"
	@echo "COMPOSE_ENV_FILE=$(COMPOSE_ENV_FILE)"
	@echo "TEST_COMPOSE_ENV_FILE=$(TEST_COMPOSE_ENV_FILE)"
	@echo "PROD_COMPOSE_ENV_FILE=$(PROD_COMPOSE_ENV_FILE)"
	@echo "PYTHON=$(PYTHON)"
	@echo "BACKEND_BUILD_FLAGS=$(BACKEND_BUILD_FLAGS)"
