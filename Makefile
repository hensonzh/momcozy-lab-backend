COMPOSE_ENV_FILE ?= env/compose.local.env
TEST_COMPOSE_ENV_FILE ?= env/compose.test.env
BACKEND_ENV_FILE ?= $(COMPOSE_ENV_FILE)
PYTHON ?= .venv/bin/python
BACKEND_BUILD_FLAGS ?=
COMPOSE = MOMCOZY_BACKEND_ENV_FILE=$(COMPOSE_ENV_FILE) docker compose -f docker-compose.local.yml
TEST_COMPOSE = MOMCOZY_BACKEND_ENV_FILE=$(TEST_COMPOSE_ENV_FILE) docker compose --env-file $(TEST_COMPOSE_ENV_FILE) -f docker-compose.test.yml

.PHONY: backend-local-build backend-build backend-local-up backend-up backend-down backend-local-migrate backend-migrate backend-local-minio backend-test-pull backend-test-migrate backend-test-up backend-test-services backend-test-down backend-test-reset backend-test-ps backend-test-logs backend-export-contracts backend-check-infra backend-productization-status backend-smoke backend-test-smoke backend-env-print

backend-local-build:
	$(MAKE) backend-build

backend-build:
	$(COMPOSE) --profile tools build $(BACKEND_BUILD_FLAGS) migrate api

backend-local-up:
	$(MAKE) backend-up

backend-up:
	$(MAKE) backend-build COMPOSE_ENV_FILE=$(COMPOSE_ENV_FILE) BACKEND_BUILD_FLAGS="$(BACKEND_BUILD_FLAGS)"
	$(COMPOSE) up -d postgres redis minio minio-init
	$(COMPOSE) --profile tools run --rm migrate
	$(COMPOSE) up -d --force-recreate api

backend-down:
	$(COMPOSE) down

backend-local-migrate:
	$(MAKE) backend-migrate

backend-migrate:
	$(COMPOSE) --profile tools build $(BACKEND_BUILD_FLAGS) migrate
	$(COMPOSE) --profile tools run --rm migrate

backend-local-minio:
	$(COMPOSE) up -d minio minio-init

backend-test-pull backend-test-migrate backend-test-up backend-test-services backend-test-down backend-test-reset:
	@echo "Direct test mutation is disabled; use the protected backend-test-delivery workflow or an approved maintenance runbook." >&2
	@exit 2

backend-test-ps:
	docker ps --filter label=com.docker.compose.project=momcozy-lab-backend-test

backend-test-logs:
	docker logs --follow $$(docker ps --quiet --filter label=com.docker.compose.project=momcozy-lab-backend-test --filter label=com.docker.compose.service=api)

backend-export-contracts:
	$(PYTHON) scripts/export_openapi.py --output docs/openapi.generated.json
	$(PYTHON) scripts/export_api_surface_catalog.py --openapi-input docs/openapi.generated.json --output docs/api-surface-catalog.md

backend-check-infra:
	set -a; . $(BACKEND_ENV_FILE); set +a; \
	$(PYTHON) scripts/check_database_profile.py; \
	$(PYTHON) scripts/check_redis_profile.py; \
	$(PYTHON) scripts/check_object_storage_profile.py; \
	$(PYTHON) scripts/check_product_asset_storage.py

backend-productization-status:
	$(PYTHON) scripts/check_productization_status.py

backend-smoke:
	$(PYTHON) scripts/check_productization_status.py
	$(PYTHON) -m pytest -q tests/test_agent_diary_internal_api.py tests/test_agent_file_internal_api.py tests/test_agent_lactation_internal_api.py tests/test_agent_plans_internal_api.py tests/test_agent_profile_internal_api.py tests/test_auth_jwks.py

backend-test-smoke:
	$(MAKE) backend-productization-status
	$(MAKE) backend-check-infra BACKEND_ENV_FILE=$(TEST_COMPOSE_ENV_FILE)

backend-env-print:
	@echo "BACKEND_ENV_FILE=$(BACKEND_ENV_FILE)"
	@echo "COMPOSE_ENV_FILE=$(COMPOSE_ENV_FILE)"
	@echo "TEST_COMPOSE_ENV_FILE=$(TEST_COMPOSE_ENV_FILE)"
	@echo "PYTHON=$(PYTHON)"
	@echo "BACKEND_BUILD_FLAGS=$(BACKEND_BUILD_FLAGS)"
