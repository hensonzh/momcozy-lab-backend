from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LOCAL_ENV = ROOT / "env" / "local.env.example"
STAGING_ENV = ROOT / "env" / "staging.env.example"
PRODUCTION_ENV = ROOT / "env" / "production.env.example"
LOCAL_COMPOSE = ROOT / "docker-compose.local.yml"
DEPLOY_COMPOSE = ROOT / "docker-compose.deploy.yml"
CI_COMPOSE = ROOT / "docker-compose.ci.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "backend-ci.yml"
DELIVERY_WORKFLOW = ROOT / ".github" / "workflows" / "backend-delivery.yml"
POSTGRES_INIT = ROOT / "deploy" / "shared" / "init-postgres.sh"
REDIS_START = ROOT / "deploy" / "shared" / "start-redis.sh"
PRODUCT_MINIO_POLICY = ROOT / "deploy" / "shared" / "minio-product-policy.json"
AGENT_MINIO_POLICY = ROOT / "deploy" / "shared" / "minio-agent-policy.json"


def test_compose_profiles_have_single_clear_role() -> None:
    local = LOCAL_COMPOSE.read_text()
    deploy = DEPLOY_COMPOSE.read_text()
    ci = CI_COMPOSE.read_text()

    assert local.startswith("name: momcozy-lab-backend-local\n")
    assert "image: momcozy-lab-backend:local" in local
    assert "env/local.env" in local
    assert "name: momcozy-lab-backend-ci" in ci
    assert "name: ${MOMCOZY_BACKEND_COMPOSE_PROJECT:?" in deploy
    assert "${MOMCOZY_BACKEND_IMAGE:?" in deploy
    assert "build:" not in deploy


def test_deploy_compose_is_environment_neutral_and_private() -> None:
    compose = DEPLOY_COMPOSE.read_text()

    assert "MOMCOZY_TEST_" not in compose
    assert "docker-compose.test.yml" not in compose
    assert "${MOMCOZY_BACKEND_API_BIND:?" in compose
    assert "MOMCOZY_NETWORK_NAME" in compose
    assert "MOMCOZY_BACKEND_COMPOSE_PROJECT" in compose
    assert "5432:5432" not in compose
    assert "6379:6379" not in compose
    assert "9000:9000" not in compose
    assert "postgres:16" in compose
    assert "redis:7.4-alpine" in compose
    assert "minio/minio:RELEASE.2025-07-23T15-54-02Z" in compose
    assert "read_only" not in compose.split("  postgres:", 1)[1]


def test_shared_infrastructure_uses_service_scoped_credentials() -> None:
    compose = DEPLOY_COMPOSE.read_text()
    postgres = POSTGRES_INIT.read_text()
    redis = REDIS_START.read_text()

    for name in (
        "MOMCOZY_POSTGRES_ADMIN_PASSWORD",
        "MOMCOZY_PRODUCT_POSTGRES_PASSWORD",
        "MOMCOZY_AGENT_POSTGRES_PASSWORD",
        "MOMCOZY_REDIS_ADMIN_PASSWORD",
        "MOMCOZY_PRODUCT_REDIS_PASSWORD",
        "MOMCOZY_AGENT_REDIS_PASSWORD",
        "MOMCOZY_MINIO_ROOT_USER",
        "MOMCOZY_MINIO_ROOT_PASSWORD",
        "MOMCOZY_PRODUCT_MINIO_ACCESS_KEY",
        "MOMCOZY_PRODUCT_MINIO_SECRET_KEY",
        "MOMCOZY_AGENT_MINIO_ACCESS_KEY",
        "MOMCOZY_AGENT_MINIO_SECRET_KEY",
    ):
        assert name in compose
    assert "MOMCOZY_TEST_" not in compose + postgres + redis
    assert "user default off" in redis
    assert "user deployment-admin" in redis
    assert "user product-backend" in redis
    assert "user agent-runtime" in redis
    assert "CREATE ROLE %I" in postgres
    assert "CREATE DATABASE %I" in postgres


def test_minio_policies_are_environment_parameterized() -> None:
    compose = DEPLOY_COMPOSE.read_text()
    product_policy = PRODUCT_MINIO_POLICY.read_text()
    agent_policy = AGENT_MINIO_POLICY.read_text()

    assert "__PRODUCT_BUCKET__" in product_policy
    assert "__AGENT_BUCKET__" in agent_policy
    assert "sed \"s/__PRODUCT_BUCKET__/" in compose
    assert "sed \"s/__AGENT_BUCKET__/" in compose
    assert "mc mb --ignore-existing" in compose
    assert "mc admin policy attach" in compose


def test_environment_templates_are_complete_and_non_secret() -> None:
    local = LOCAL_ENV.read_text()
    staging = STAGING_ENV.read_text()
    production = PRODUCTION_ENV.read_text()

    assert "APP_ENV=local" in local
    assert "APP_ENV=staging" in staging
    assert "APP_ENV=production" in production
    assert "MOMCOZY_NETWORK_NAME=momcozy-lab-staging" in staging
    assert "MOMCOZY_NETWORK_NAME=momcozy-lab-production" in production
    assert "MOMCOZY_PRODUCT_POSTGRES_DB=momcozy_staging" in staging
    assert "MOMCOZY_PRODUCT_POSTGRES_DB=momcozy_production" in production
    assert "backend-test.lute-momcozylab.luteos.cloud" in staging
    assert "product-api.example.com" in production
    assert "MOMCOZY_TEST_" not in staging + production
    assert "sk-" not in staging + production


def test_ci_and_delivery_use_canonical_entrypoints() -> None:
    ci = CI_WORKFLOW.read_text()
    delivery = DELIVERY_WORKFLOW.read_text()

    assert "docker-compose.deploy.yml" in ci
    assert "env/staging.env.example" in ci
    assert "python scripts/release.py image-manifest" in ci
    assert "scripts/test_release.py" not in ci + delivery
    assert "name: ${{ inputs.environment }}" in delivery
    assert "options:\n          - staging\n          - production" in delivery
    assert "--environment '${DEPLOY_ENVIRONMENT}'" in delivery
    assert "secrets.SSH_KNOWN_HOSTS" in delivery


def test_makefile_exposes_validation_but_not_direct_deploy_mutation() -> None:
    makefile = (ROOT / "Makefile").read_text()

    assert "LOCAL_ENV_FILE ?= env/local.env" in makefile
    assert "DEPLOY_ENV_FILE ?= env/$(DEPLOY_ENVIRONMENT).env" in makefile
    assert "backend-staging-config" in makefile
    assert "backend-production-config" in makefile
    assert "backend-deploy-validate" in makefile
    assert "$(DEPLOY_COMPOSE) up" not in makefile
    assert "$(DEPLOY_COMPOSE) down" not in makefile
