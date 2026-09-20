from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_LOCAL_ENV = ROOT / "env" / "compose.local.env.example"
COMPOSE_TEST_ENV = ROOT / "env" / "compose.test.env.example"
CI_COMPOSE = ROOT / "docker-compose.ci.yml"
CI_WORKFLOW = ROOT / ".github" / "workflows" / "backend-ci.yml"
LOCAL_COMPOSE = ROOT / "docker-compose.local.yml"
TEST_COMPOSE = ROOT / "docker-compose.test.yml"
POSTGRES_INIT = ROOT / "deploy" / "test" / "init-postgres.sh"
REDIS_START = ROOT / "deploy" / "test" / "start-redis.sh"
PRODUCT_MINIO_POLICY = ROOT / "deploy" / "test" / "minio-product-policy.json"
AGENT_MINIO_POLICY = ROOT / "deploy" / "test" / "minio-agent-policy.json"
NGINX_CONFIG = (
    ROOT / "deploy" / "nginx" / "momcozy-lab-product-backend.conf"
)

RETIRED_RUNTIME_ENV_NAMES = (
    "AGENT_RUNTIME_WORKER_",
    "AGENT_RUNTIME_INTERRUPT_",
    "AGENT_RUNTIME_MAX_INLINE_PAYLOAD_BYTES",
    "OPENAI_MODEL",
    "OPENAI_REASONING_EFFORT",
    "OPENAI_RESPONSES_STORE",
    "OPENAI_AGENT_",
    "AGENT_QUICK_REPLY_",
    "AGENT_FACT_",
    "AGENT_MEMORY_CONSOLIDATION_",
)
RETIRED_DEPLOYMENT_TOKENS = (
    "agent-worker",
    "memory-worker",
    "scripts.run_agent_worker",
    "scripts.run_memory_consolidation",
    "agent-references",
    "pump-models",
)


def test_project_metadata_uses_backend_name() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text()
    ci_compose = CI_COMPOSE.read_text()
    local_compose = LOCAL_COMPOSE.read_text()
    test_compose = TEST_COMPOSE.read_text()

    assert 'name = "backend"' in pyproject
    assert "name: momcozy-lab-backend-ci" in ci_compose
    assert local_compose.startswith("name: momcozy-lab-backend-local\n")
    assert test_compose.startswith("name: momcozy-lab-backend-test\n")
    assert ci_compose.count("image: momcozy-lab-backend:ci") == 2
    assert ci_compose.count("APP_ENV: test") == 2
    assert local_compose.count("image: momcozy-lab-backend:local") == 6
    assert "${MOMCOZY_BACKEND_IMAGE:?" in test_compose
    assert "build:" not in test_compose
    assert not (ROOT / "docker-compose.production.yml").exists()
    assert not (ROOT / "env" / "compose.production.env.example").exists()
    assert (
        ROOT / ".github" / "workflows" / "backend-ci.yml"
    ).exists()
    assert not (
        ROOT
        / ".github"
        / "workflows"
        / "production-backend-ci.yml"
    ).exists()


def test_test_shared_services_enforce_service_credentials_and_resource_limits() -> None:
    compose = TEST_COMPOSE.read_text()
    env = COMPOSE_TEST_ENV.read_text()
    redis_start = REDIS_START.read_text()

    assert "MOMCOZY_TEST_REDIS_PASSWORD" not in compose + env
    assert "redis://product-backend:${MOMCOZY_TEST_PRODUCT_REDIS_PASSWORD:?" in compose
    assert "MOMCOZY_TEST_REDIS_ADMIN_PASSWORD" in compose + env
    assert "MOMCOZY_TEST_AGENT_REDIS_PASSWORD" in compose + env
    assert "user default off" in redis_start
    assert "user product-backend" in redis_start
    assert "~rate-limit:*" in redis_start
    assert "~product:agent-asset-capability:v1:*" in redis_start
    assert "user agent-runtime" in redis_start
    assert "~agent-runtime:*" in redis_start
    assert "~momcozy-agent-runtime:*" in redis_start
    assert "-@dangerous" in redis_start

    assert "MOMCOZY_TEST_PRODUCT_MINIO_ACCESS_KEY" in compose + env
    assert "MOMCOZY_TEST_AGENT_MINIO_ACCESS_KEY" in compose + env
    assert "OBJECT_STORAGE_ACCESS_KEY_ID: ${MOMCOZY_TEST_PRODUCT_MINIO_ACCESS_KEY" in compose
    assert "mc admin user add" in compose
    assert "mc admin policy attach" in compose
    assert "momcozy-test" in PRODUCT_MINIO_POLICY.read_text()
    assert "agent-runtime-test" not in PRODUCT_MINIO_POLICY.read_text()
    assert "agent-runtime-test" in AGENT_MINIO_POLICY.read_text()
    assert "momcozy-test" not in AGENT_MINIO_POLICY.read_text()

    for cpu, memory in (
        ("cpus: 1.0", "mem_limit: 1g"),
        ("cpus: 1.0", "mem_limit: 1536m"),
        ("cpus: 0.25", "mem_limit: 256m"),
        ("cpus: 0.5", "mem_limit: 1g"),
    ):
        assert cpu in compose
        assert memory in compose


def test_test_template_contains_no_deployable_credentials_or_stale_image() -> None:
    env = COMPOSE_TEST_ENV.read_text()
    release_script = (ROOT / "scripts" / "test_release.py").read_text()

    assert "MOMCOZY_BACKEND_IMAGE=" not in env
    assert "SERVICE_API_KEY=\n" in env
    assert "AGENT_RUNTIME_SERVICE_API_KEY=\n" in env
    assert "AUTH_INVITE_CODES=\n" in env
    assert "MOMCOZY-BETA" not in env
    assert "test-service-key-with-at-least-32-bytes" not in env
    assert "test-agent-runtime-service-key-with-at-least-32-bytes" not in env
    assert "test-service-key-with-at-least-32-bytes" in release_script
    assert "test-agent-runtime-service-key-with-at-least-32-bytes" in release_script
    assert "MOMCOZY_TEST_POSTGRES_ADMIN_PASSWORD" in release_script
    assert "MOMCOZY_TEST_PRODUCT_MINIO_SECRET_KEY" in release_script


def _environment_names(text: str) -> set[str]:
    return {
        line.partition("=")[0].strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#") and "=" in line
    }


def test_dockerfile_runs_product_backend() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "python:3.13-slim" in dockerfile
    assert "requirements.txt" in dockerfile
    assert "app.main:app" in dockerfile
    assert '"--no-access-log"' in dockerfile
    assert "COPY --chown=app:app . ." in dockerfile
    assert "chown -R" not in dockerfile


def test_ci_compose_is_a_secret_safe_runtime_override() -> None:
    compose = CI_COMPOSE.read_text()

    assert "CI-only override" in compose
    assert "AUTH_JWT_PRIVATE_KEY_B64: ${AUTH_JWT_PRIVATE_KEY_B64:?" in compose
    assert "/v1/health/ready" in compose
    assert "BEGIN PRIVATE KEY" not in compose
    assert "\n  postgres:" not in compose
    assert "\n  redis:" not in compose
    # The CI infrastructure override only pins the verified upstream images.
    assert "image: quay.io/minio/minio:RELEASE.2025-07-23T15-54-02Z" in compose
    assert "image: quay.io/minio/mc:RELEASE.2025-07-21T05-28-08Z" in compose
    assert "condition: service_healthy" in compose
    assert "minio/health/live" in compose


def test_ci_container_job_builds_migrates_smokes_and_cleans_up() -> None:
    workflow = CI_WORKFLOW.read_text()
    container_job = workflow.split("  container:", maxsplit=1)[1].split(
        "\n  postgres-migration:",
        maxsplit=1,
    )[0]

    assert "openssl genpkey" in container_job
    assert "AUTH_JWT_PRIVATE_KEY_B64" in container_job
    assert '"$GITHUB_ENV"' in container_job
    assert container_job.count("-f docker-compose.ci.yml") >= 5
    assert "docker-compose.local.yml" in container_job
    assert "docker-compose.test.yml" in container_job
    assert "momcozy-lab-backend:ci" in container_job
    assert "--profile tools run --rm migrate" in container_job
    assert "--wait-timeout 90" in container_job
    assert "http://127.0.0.1:8000/v1/health/ready" in container_job
    assert "if: failure()" in container_job
    assert "if: always()" in container_job
    assert "down --volumes" in container_job
    assert "docker build -f Dockerfile ." not in container_job


def test_ci_profile_identity_and_override_usage_are_documented() -> None:
    readme = (ROOT / "README.md").read_text()
    profiles = (ROOT / "docs" / "environment-profiles.md").read_text()
    docs = readme + profiles

    assert "momcozy-lab-backend-ci" in docs
    assert "momcozy-lab-backend:ci" in docs
    assert "docker-compose.ci.yml" in docs
    assert "CI-only override" in docs
    assert "ephemeral" in docs


def test_nginx_proxy_keeps_api_private_and_supports_streaming_transports() -> None:
    config = NGINX_CONFIG.read_text()

    assert NGINX_CONFIG.name == "momcozy-lab-product-backend.conf"
    assert not (ROOT / "deploy" / "nginx" / "momcozy-api.conf").exists()
    assert "server 127.0.0.1:8001;" in config
    assert "server_name backend-test.lute-momcozylab.luteos.cloud;" in config
    assert "listen 8443 ssl http2;" in config
    assert "listen 80" not in config
    assert "listen 443" not in config
    assert "default_server" not in config
    assert "client_max_body_size 16m;" in config
    assert (
        "ssl_certificate /etc/nginx/tls/momcozy-lab-test/fullchain.pem;"
        in config
    )
    assert (
        "ssl_certificate_key "
        "/etc/nginx/tls/momcozy-lab-test/privkey.pem;" in config
    )
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in config
    assert "ssl_session_tickets off;" in config
    assert "momcozy-lab-product-backend.access.log" in config
    assert "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;" in config
    assert "proxy_set_header X-Forwarded-Proto $scheme;" in config
    assert "proxy_set_header X-Request-ID $request_id;" in config
    assert "location = /v1/realtime-voice-session" in config
    assert "proxy_set_header Upgrade $http_upgrade;" in config
    assert 'proxy_set_header Connection "upgrade";' in config
    assert "proxy_buffering off;" in config
    assert "proxy_read_timeout 3600s;" in config
    capability_location = config[
        config.index("location ^~ /v1/model-assets/") :
        config.index("\n    }", config.index("location ^~ /v1/model-assets/"))
    ]
    assert "access_log off;" in capability_location


def test_nginx_serves_android_download_artifacts_without_api_proxying() -> None:
    config = NGINX_CONFIG.read_text()

    assert "location /app/" in config
    assert "alias /var/www/momcozy/android-apk/;" in config
    assert "index index.html;" in config
    assert "autoindex off;" in config
    assert "location ~ ^/app/releases/" in config
    assert "alias /var/www/momcozy/android-apk/releases/$apk_file;" in config
    assert "application/vnd.android.package-archive" in config
    assert "Content-Disposition" in config
    assert "attachment; filename=\"$apk_file\"" in config
    assert "X-Content-Type-Options nosniff always;" in config


def test_local_compose_uses_product_infrastructure_service_names() -> None:
    compose = LOCAL_COMPOSE.read_text()
    env = COMPOSE_LOCAL_ENV.read_text()

    assert "postgres:16" in compose
    assert "redis:7" in compose
    assert "minio/minio:latest" in compose
    assert "${MOMCOZY_BACKEND_ENV_FILE:-env/compose.local.env}" in compose
    assert "postgresql+asyncpg://momcozy:momcozy@postgres:5432/momcozy" in env
    assert "redis://redis:6379/0" in env
    assert "localhost" not in env
    assert "127.0.0.1" not in env
    assert "mc mirror --overwrite /seed/product-assets" in compose
    for retired in RETIRED_DEPLOYMENT_TOKENS:
        assert retired not in compose


def test_compose_env_keeps_object_storage_switchable_by_environment() -> None:
    env = COMPOSE_LOCAL_ENV.read_text()

    assert "OBJECT_STORAGE_PROVIDER=minio" in env
    assert "OBJECT_STORAGE_LOCAL_ROOT=" in env
    assert "OBJECT_STORAGE_BUCKET=momcozy-local" in env
    assert "OBJECT_STORAGE_ENDPOINT_URL=http://minio:9000" in env
    assert "OBJECT_STORAGE_ACCESS_KEY_ID=minioadmin" in env
    assert "OBJECT_STORAGE_SECRET_ACCESS_KEY=minioadmin" in env
    assert "PRODUCT_ASSET_MANIFEST_PATH=/workspace/assets/product-assets.manifest.json" in env
    assert "PRODUCT_ASSET_LOCAL_ROOT=" in env


def test_environment_profiles_keep_product_runtime_boundary_only() -> None:
    env_dir = ROOT / "env"

    assert (env_dir / "compose.local.env.example").exists()
    assert (env_dir / "compose.test.env.example").exists()
    assert not (env_dir / "compose.staging.env.example").exists()
    assert not (ROOT / "docker-compose.staging.yml").exists()
    assert not (env_dir / "compose.production.env.example").exists()
    assert not (env_dir / "compose.prod.env.example").exists()
    assert not (ROOT / "docker-compose.prod.yml").exists()
    assert not (env_dir / "local.env.example").exists()
    assert not (env_dir / "test.env.example").exists()
    assert not (env_dir / "production.env.example").exists()

    for env_path in (COMPOSE_LOCAL_ENV, COMPOSE_TEST_ENV):
        env = env_path.read_text()
        names = _environment_names(env)
        assert "AGENT_RUNTIME_SERVICE_API_KEY=" in env
        assert "AGENT_MODEL_ASSET_PUBLIC_BASE_URL=" in env
        assert "AGENT_MODEL_ASSET_INACTIVITY_TTL_SECONDS=" in env
        assert "OPENAI_API_KEY=" in env
        assert "VISION_OPENAI_MODEL=gpt-5.4-mini" in env
        for retired in RETIRED_RUNTIME_ENV_NAMES:
            if retired.endswith("_"):
                assert not any(name.startswith(retired) for name in names)
            else:
                assert retired not in names


def test_environment_profiles_use_asymmetric_user_jwt_contract() -> None:
    for env_path in (COMPOSE_LOCAL_ENV, COMPOSE_TEST_ENV):
        env = env_path.read_text()

        assert "AUTH_JWT_PRIVATE_KEY_B64=" in env
        assert "AUTH_JWT_ISSUER=" in env
        assert "AUTH_JWT_PRODUCT_AUDIENCE=momcozy-product-api" in env
        assert "AUTH_JWT_RUNTIME_AUDIENCE=momcozy-agent-runtime" in env
        for retired_name in (
            "AUTH_JWT_" + "SECRET",
            "AUTH_JWT_" + "ALGORITHM",
            "AUTH_JWT_" + "AUDIENCE=",
        ):
            assert retired_name not in env


def test_backend_installs_pyjwt_crypto_support() -> None:
    requirements = (ROOT / "requirements.txt").read_text()

    assert "PyJWT[crypto]==2.13.0" in requirements
    assert "\nPyJWT==2.13.0" not in requirements


def test_makefile_builds_only_product_api_and_migration_services() -> None:
    makefile = (ROOT / "Makefile").read_text()

    assert "COMPOSE_ENV_FILE ?= env/compose.local.env" in makefile
    assert "MOMCOZY_BACKEND_ENV_FILE=$(COMPOSE_ENV_FILE)" in makefile
    assert "PYTHON ?= .venv/bin/python" in makefile
    assert "$(COMPOSE) --profile tools build $(BACKEND_BUILD_FLAGS) migrate api" in makefile
    assert "$(COMPOSE) up -d postgres redis minio minio-init" in makefile
    assert "$(COMPOSE) --profile tools run --rm migrate" in makefile
    assert "$(COMPOSE) up -d --force-recreate api" in makefile
    assert "$(PYTHON) scripts/check_database_profile.py" in makefile
    assert "$(PYTHON) scripts/check_redis_profile.py" in makefile
    assert "$(PYTHON) scripts/check_object_storage_profile.py" in makefile
    assert "$(PYTHON) scripts/check_product_asset_storage.py" in makefile
    assert "backend-productization-status:" in makefile
    assert "backend-test-smoke:" in makefile
    assert "backend-production" not in makefile
    assert "PRODUCTION_COMPOSE" not in makefile
    for retired in (
        "backend-workers",
        "backend-local-workers",
        "backend-worker-backlog",
        "backend-agent-recover-stuck-runs",
        "backend-agent-device-decision-eval",
        "backend-publish-pump-models-reference",
        "check_redis_runtime_controls.py",
        "inspect_worker_backlog.py",
        "recover_stuck_agent_runs.py",
        "run_agent_fact_eval.py",
    ):
        assert retired not in makefile


def test_docker_context_excludes_generated_and_private_artifacts() -> None:
    dockerignore = (ROOT / ".dockerignore").read_text().splitlines()

    for pattern in [
        "fixtures/product_assets",
        "**/.venv",
        "**/.local",
        "**/.pytest_cache",
        "**/.mypy_cache",
        "**/.ruff_cache",
        "**/__pycache__",
        "**/*.pyc",
        "env/*.env",
        "!env/*.env.example",
    ]:
        assert pattern in dockerignore


def test_product_media_provider_configuration_is_preserved() -> None:
    local_env = COMPOSE_LOCAL_ENV.read_text()
    test_env = COMPOSE_TEST_ENV.read_text()

    assert "OPENAI_API_KEY=" in local_env
    assert "VOICE_PROVIDER=disabled" in local_env
    assert "VOICE_API_KEY=" in local_env
    assert "VOICE_BASE_URL=wss://openspeech.bytedance.com/api/v3/tts/bidirection" in local_env
    assert "VOICE_TRANSCRIBE_MODEL=" in local_env
    assert "VOICE_TTS_RESOURCE_ID=seed-tts-2.0" in local_env
    assert "VOICE_REQUEST_TIMEOUT_SECONDS=30" in local_env
    assert "VISION_PROVIDER=disabled" in local_env
    assert "VOICE_PROVIDER=disabled" in test_env
    assert "VISION_PROVIDER=disabled" in test_env


def test_deployment_templates_have_no_embedded_runtime_processes_or_assets() -> None:
    paths = [
        COMPOSE_LOCAL_ENV,
        COMPOSE_TEST_ENV,
        LOCAL_COMPOSE,
        TEST_COMPOSE,
        ROOT / "Makefile",
    ]

    for path in paths:
        text = path.read_text()
        for retired in RETIRED_DEPLOYMENT_TOKENS:
            assert retired not in text, f"{path.relative_to(ROOT)} contains {retired}"


def test_compose_exposes_minio_as_default_local_object_storage() -> None:
    compose = LOCAL_COMPOSE.read_text()

    assert "minio:" in compose
    minio_section = compose.split("  minio:", maxsplit=1)[1].split("\n  minio-init:", maxsplit=1)[0]
    assert "profiles:" not in minio_section
    assert "server /data --address" in compose
    assert "9000:9000" in compose
    assert "minio-init:" in compose
    assert "mc mb --ignore-existing local/momcozy-local" in compose


def test_test_compose_owns_shared_containerized_infrastructure() -> None:
    compose = TEST_COMPOSE.read_text()
    init_script = POSTGRES_INIT.read_text()

    assert "postgres:16" in compose
    assert "redis:7" in compose
    assert "minio/minio:RELEASE.2025-07-23T15-54-02Z" in compose
    assert "postgres_test_data:" in compose
    assert "redis_test_data:" in compose
    assert "minio_test_data:" in compose
    assert "5432:5432" not in compose
    assert "6379:6379" not in compose
    assert "9000:9000" not in compose
    assert "mc mb --ignore-existing test/momcozy-test" in compose
    assert "mc mb --ignore-existing test/agent-runtime-test" in compose
    assert "name: momcozy-lab-test" in compose
    assert "test-postgres" in compose
    assert "test-redis" in compose
    assert "test-minio" in compose
    assert "product-backend" in compose
    assert "momcozy_test" in init_script
    assert "agent_runtime_test" in init_script
    assert "MOMCOZY_TEST_PRODUCT_POSTGRES_PASSWORD" in init_script
    assert "MOMCOZY_TEST_AGENT_POSTGRES_PASSWORD" in init_script
    assert "SELECT count(*) FROM pg_database" in compose
    for required_secret in (
        "MOMCOZY_TEST_POSTGRES_ADMIN_PASSWORD",
        "MOMCOZY_TEST_PRODUCT_POSTGRES_PASSWORD",
        "MOMCOZY_TEST_AGENT_POSTGRES_PASSWORD",
        "MOMCOZY_TEST_REDIS_ADMIN_PASSWORD",
        "MOMCOZY_TEST_PRODUCT_REDIS_PASSWORD",
        "MOMCOZY_TEST_AGENT_REDIS_PASSWORD",
        "MOMCOZY_TEST_MINIO_ROOT_USER",
        "MOMCOZY_TEST_MINIO_ROOT_PASSWORD",
        "MOMCOZY_TEST_PRODUCT_MINIO_ACCESS_KEY",
        "MOMCOZY_TEST_PRODUCT_MINIO_SECRET_KEY",
        "MOMCOZY_TEST_AGENT_MINIO_ACCESS_KEY",
        "MOMCOZY_TEST_AGENT_MINIO_SECRET_KEY",
    ):
        assert f"${{{required_secret}:?" in compose
        assert f'{required_secret}: ""' in compose
    for retired in RETIRED_DEPLOYMENT_TOKENS:
        assert retired not in compose


def test_test_compose_uses_test_env_and_safe_api_bind() -> None:
    compose = TEST_COMPOSE.read_text()
    env = COMPOSE_TEST_ENV.read_text()

    assert "${MOMCOZY_BACKEND_ENV_FILE:-env/compose.test.env}" in compose
    assert "${MOMCOZY_TEST_API_BIND:-127.0.0.1:8001}:8000" in compose
    assert "APP_ENV=test" in env
    assert 'APP_NAME="Product Backend"' in env
    assert (
        "AGENT_MODEL_ASSET_PUBLIC_BASE_URL="
        "https://backend-test.lute-momcozylab.luteos.cloud:8443" in env
    )
    assert (
        "TRUSTED_HOSTS=backend-test.lute-momcozylab.luteos.cloud,"
        "product-backend,localhost,127.0.0.1" in env
    )
    assert (
        "DATABASE_URL=postgresql+asyncpg://momcozy_test:"
        "${MOMCOZY_TEST_PRODUCT_POSTGRES_PASSWORD}"
        "@test-postgres:5432/momcozy_test"
    ) in env
    assert (
        "REDIS_URL=redis://product-backend:"
        "${MOMCOZY_TEST_PRODUCT_REDIS_PASSWORD}@test-redis:6379/0"
    ) in env
    assert "OBJECT_STORAGE_PROVIDER=minio" in env
    assert "OBJECT_STORAGE_BUCKET=momcozy-test" in env
    assert "OBJECT_STORAGE_ENDPOINT_URL=http://test-minio:9000" in env


def test_makefile_blocks_direct_test_mutation() -> None:
    makefile = (ROOT / "Makefile").read_text()

    assert "PRODUCTION_COMPOSE" not in makefile
    assert "backend-production" not in makefile

    assert "TEST_COMPOSE_ENV_FILE ?= env/compose.test.env" in makefile
    assert "--env-file $(TEST_COMPOSE_ENV_FILE)" in makefile
    assert "$(TEST_COMPOSE) build" not in makefile
    assert "backend-test-services" in makefile
    assert "backend-test-reset:" in makefile
    assert "Direct test mutation is disabled" in makefile
    assert "docker ps --filter label=com.docker.compose.project=momcozy-lab-backend-test" in makefile
    assert "$(TEST_COMPOSE) up" not in makefile
    assert "$(TEST_COMPOSE) down --volumes" not in makefile
