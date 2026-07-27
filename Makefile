COMPOSE_ENV_FILE ?= env/compose.local.env
TEST_COMPOSE_ENV_FILE ?= env/compose.test.env
PROD_COMPOSE_ENV_FILE ?= env/compose.prod.env
BACKEND_ENV_FILE ?= $(COMPOSE_ENV_FILE)
PYTHON ?= .venv/bin/python
BACKEND_BUILD_FLAGS ?=
COMPOSE = MOMCOZY_BACKEND_ENV_FILE=$(COMPOSE_ENV_FILE) docker compose -f docker-compose.local.yml
TEST_COMPOSE = MOMCOZY_BACKEND_ENV_FILE=$(TEST_COMPOSE_ENV_FILE) docker compose -f docker-compose.test.yml
PROD_COMPOSE = MOMCOZY_BACKEND_ENV_FILE=$(PROD_COMPOSE_ENV_FILE) docker compose -f docker-compose.prod.yml

.PHONY: backend-local-build backend-build backend-local-up backend-up backend-down backend-local-migrate backend-migrate backend-local-minio backend-test-build backend-test-migrate backend-test-up backend-test-services backend-test-down backend-test-reset backend-test-ps backend-test-logs backend-prod-build backend-prod-migrate backend-prod-up backend-prod-services backend-prod-down backend-prod-ps backend-prod-logs backend-export-contracts backend-check-infra backend-productization-status backend-smoke backend-test-smoke backend-prod-readiness backend-env-print

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

backend-test-build:
	$(TEST_COMPOSE) --profile tools build $(BACKEND_BUILD_FLAGS) migrate api

backend-test-migrate:
	$(TEST_COMPOSE) --profile tools build $(BACKEND_BUILD_FLAGS) migrate
	$(TEST_COMPOSE) --profile tools run --rm migrate

backend-test-up:
	$(MAKE) backend-test-build TEST_COMPOSE_ENV_FILE=$(TEST_COMPOSE_ENV_FILE) BACKEND_BUILD_FLAGS="$(BACKEND_BUILD_FLAGS)"
	$(TEST_COMPOSE) up -d postgres redis minio minio-init
	$(MAKE) backend-test-migrate TEST_COMPOSE_ENV_FILE=$(TEST_COMPOSE_ENV_FILE)
	$(TEST_COMPOSE) up -d --force-recreate api

backend-test-services:
	$(TEST_COMPOSE) build $(BACKEND_BUILD_FLAGS) api
	$(TEST_COMPOSE) up -d --force-recreate api

backend-test-down:
	$(TEST_COMPOSE) down

backend-test-reset:
	$(TEST_COMPOSE) down --volumes --remove-orphans

backend-test-ps:
	$(TEST_COMPOSE) ps

backend-test-logs:
	$(TEST_COMPOSE) logs -f api

backend-prod-build:
	$(PROD_COMPOSE) --profile tools build $(BACKEND_BUILD_FLAGS) migrate api

backend-prod-migrate:
	$(PROD_COMPOSE) --profile tools build $(BACKEND_BUILD_FLAGS) migrate
	$(PROD_COMPOSE) --profile tools run --rm migrate

backend-prod-up:
	$(MAKE) backend-prod-build PROD_COMPOSE_ENV_FILE=$(PROD_COMPOSE_ENV_FILE) BACKEND_BUILD_FLAGS="$(BACKEND_BUILD_FLAGS)"
	$(MAKE) backend-prod-migrate PROD_COMPOSE_ENV_FILE=$(PROD_COMPOSE_ENV_FILE)
	$(PROD_COMPOSE) up -d --force-recreate api

backend-prod-services:
	$(PROD_COMPOSE) build $(BACKEND_BUILD_FLAGS) api
	$(PROD_COMPOSE) up -d --force-recreate api

backend-prod-down:
	$(PROD_COMPOSE) down

backend-prod-ps:
	$(PROD_COMPOSE) ps

backend-prod-logs:
	$(PROD_COMPOSE) logs -f api

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

backend-prod-readiness:
	$(MAKE) backend-productization-status
	$(MAKE) backend-check-infra BACKEND_ENV_FILE=$(PROD_COMPOSE_ENV_FILE)

backend-env-print:
	@echo "BACKEND_ENV_FILE=$(BACKEND_ENV_FILE)"
	@echo "COMPOSE_ENV_FILE=$(COMPOSE_ENV_FILE)"
	@echo "TEST_COMPOSE_ENV_FILE=$(TEST_COMPOSE_ENV_FILE)"
	@echo "PROD_COMPOSE_ENV_FILE=$(PROD_COMPOSE_ENV_FILE)"
	@echo "PYTHON=$(PYTHON)"
	@echo "BACKEND_BUILD_FLAGS=$(BACKEND_BUILD_FLAGS)"
