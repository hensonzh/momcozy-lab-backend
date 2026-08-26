COMPOSE_ENV_FILE ?= env/compose.local.env
STAGING_COMPOSE_ENV_FILE ?= env/compose.staging.env
BACKEND_ENV_FILE ?= $(COMPOSE_ENV_FILE)
PYTHON ?= .venv/bin/python
BACKEND_BUILD_FLAGS ?=
COMPOSE = MOMCOZY_BACKEND_ENV_FILE=$(COMPOSE_ENV_FILE) docker compose -f docker-compose.local.yml
STAGING_COMPOSE = MOMCOZY_BACKEND_ENV_FILE=$(STAGING_COMPOSE_ENV_FILE) docker compose --env-file $(STAGING_COMPOSE_ENV_FILE) -f docker-compose.staging.yml

.PHONY: backend-local-build backend-build backend-local-up backend-up backend-down backend-local-migrate backend-migrate backend-local-minio backend-staging-pull backend-staging-migrate backend-staging-up backend-staging-services backend-staging-down backend-staging-reset backend-staging-ps backend-staging-logs backend-export-contracts backend-check-infra backend-productization-status backend-smoke backend-staging-smoke backend-env-print

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

backend-staging-pull backend-staging-migrate backend-staging-up backend-staging-services backend-staging-down backend-staging-reset:
	@echo "Direct staging mutation is disabled; use the protected backend-staging-delivery workflow or an approved maintenance runbook." >&2
	@exit 2

backend-staging-ps:
	docker ps --filter label=com.docker.compose.project=momcozy-lab-backend-staging

backend-staging-logs:
	docker logs --follow $$(docker ps --quiet --filter label=com.docker.compose.project=momcozy-lab-backend-staging --filter label=com.docker.compose.service=api)

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

backend-staging-smoke:
	$(MAKE) backend-productization-status
	$(MAKE) backend-check-infra BACKEND_ENV_FILE=$(STAGING_COMPOSE_ENV_FILE)

backend-env-print:
	@echo "BACKEND_ENV_FILE=$(BACKEND_ENV_FILE)"
	@echo "COMPOSE_ENV_FILE=$(COMPOSE_ENV_FILE)"
	@echo "STAGING_COMPOSE_ENV_FILE=$(STAGING_COMPOSE_ENV_FILE)"
	@echo "PYTHON=$(PYTHON)"
	@echo "BACKEND_BUILD_FLAGS=$(BACKEND_BUILD_FLAGS)"
