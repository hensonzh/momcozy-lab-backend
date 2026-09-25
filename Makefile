LOCAL_ENV_FILE ?= env/local.env
DEPLOY_ENVIRONMENT ?= staging
DEPLOY_ENV_FILE ?= env/$(DEPLOY_ENVIRONMENT).env
BACKEND_ENV_FILE ?= $(LOCAL_ENV_FILE)
DEPLOY_IMAGE ?= ghcr.io/hensonzh/momcozy-lab-backend@sha256:0000000000000000000000000000000000000000000000000000000000000000
PYTHON ?= .venv/bin/python
BACKEND_BUILD_FLAGS ?=
LOCAL_COMPOSE = MOMCOZY_BACKEND_ENV_FILE=$(LOCAL_ENV_FILE) docker compose -f docker-compose.local.yml
DEPLOY_COMPOSE = MOMCOZY_BACKEND_IMAGE=$(DEPLOY_IMAGE) MOMCOZY_BACKEND_ENV_FILE=$(DEPLOY_ENV_FILE) docker compose --env-file $(DEPLOY_ENV_FILE) -f docker-compose.deploy.yml

.PHONY: \
	backend-local-build backend-build backend-local-up backend-up backend-down \
	backend-local-migrate backend-migrate backend-local-minio backend-local-account \
	backend-deploy-validate backend-staging-config backend-production-config \
	backend-deploy-ps backend-deploy-logs backend-export-contracts \
	backend-check-infra backend-productization-status backend-smoke backend-env-print

backend-local-build:
	$(MAKE) backend-build

backend-build:
	$(LOCAL_COMPOSE) --profile tools build $(BACKEND_BUILD_FLAGS) migrate api

backend-local-up:
	$(MAKE) backend-up

backend-up:
	$(MAKE) backend-build LOCAL_ENV_FILE=$(LOCAL_ENV_FILE) BACKEND_BUILD_FLAGS="$(BACKEND_BUILD_FLAGS)"
	$(LOCAL_COMPOSE) up -d postgres redis minio minio-init
	$(LOCAL_COMPOSE) --profile tools run --rm migrate
	$(LOCAL_COMPOSE) up -d --force-recreate api

backend-down:
	$(LOCAL_COMPOSE) down

backend-local-migrate:
	$(MAKE) backend-migrate

backend-migrate:
	$(LOCAL_COMPOSE) --profile tools build $(BACKEND_BUILD_FLAGS) migrate
	$(LOCAL_COMPOSE) --profile tools run --rm migrate

backend-local-minio:
	$(LOCAL_COMPOSE) up -d minio minio-init

backend-local-account:
	$(LOCAL_COMPOSE) exec -T api python < scripts/seed_local_test_account.py

# Deployment mutation is intentionally owned by the protected backend-delivery workflow.
backend-deploy-validate:
	$(DEPLOY_COMPOSE) config --quiet

backend-staging-config:
	$(MAKE) backend-deploy-validate DEPLOY_ENVIRONMENT=staging DEPLOY_ENV_FILE=env/staging.env.example

backend-production-config:
	$(MAKE) backend-deploy-validate DEPLOY_ENVIRONMENT=production DEPLOY_ENV_FILE=env/production.env.example

backend-deploy-ps:
	$(DEPLOY_COMPOSE) ps

backend-deploy-logs:
	$(DEPLOY_COMPOSE) logs --follow api

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

backend-env-print:
	@echo "BACKEND_ENV_FILE=$(BACKEND_ENV_FILE)"
	@echo "LOCAL_ENV_FILE=$(LOCAL_ENV_FILE)"
	@echo "DEPLOY_ENVIRONMENT=$(DEPLOY_ENVIRONMENT)"
	@echo "DEPLOY_ENV_FILE=$(DEPLOY_ENV_FILE)"
	@echo "PYTHON=$(PYTHON)"
	@echo "BACKEND_BUILD_FLAGS=$(BACKEND_BUILD_FLAGS)"
